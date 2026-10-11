import type { AgentTool } from '@earendil-works/pi-agent-core';
import { Type, type TSchema } from '@earendil-works/pi-ai';
import type { ItemMessage, JsonObject } from './protocol.js';
import type { RpcCaller } from './credentials.js';

export const MAX_READ_CHARS = 8000;
export const MAX_SUBMISSIONS = 3;
const SNIPPET = 80;
const MAX_MATCHES = 8;

export interface SubmissionState {
  payload: JsonObject | null;
  attempts: number;
  rejected: boolean;
}

function text(value: string) {
  return { content: [{ type: 'text' as const, text: value }], details: {} };
}

/** Fail the tool call with a fixed message; the model sees it as an error. */
class ToolFailure extends Error {}

export function buildTools(
  item: ItemMessage,
  rpc: RpcCaller,
  submission: SubmissionState,
): AgentTool<TSchema>[] {
  const sections = new Map(item.sections.map((s) => [s.name, s.text]));
  const tools: AgentTool<TSchema>[] = [
    {
      name: 'list_sections',
      label: 'List sections',
      description:
        'List the saved posting sections available to read, with their character counts.',
      parameters: Type.Object({}, { additionalProperties: false }),
      execute: async () =>
        text(
          JSON.stringify(
            item.sections.map((s) => ({ name: s.name, chars: s.text.length })),
          ),
        ),
    },
    {
      name: 'read_section',
      label: 'Read section',
      description: `Read part of one saved section by exact name. Returns at most ${MAX_READ_CHARS} characters starting at offset.`,
      parameters: Type.Object(
        {
          name: Type.String({ minLength: 1, maxLength: 120 }),
          offset: Type.Optional(Type.Integer({ minimum: 0 })),
          limit: Type.Optional(
            Type.Integer({ minimum: 1, maximum: MAX_READ_CHARS }),
          ),
        },
        { additionalProperties: false },
      ),
      execute: async (_id, params) => {
        const args = params as {
          name: string;
          offset?: number;
          limit?: number;
        };
        const source = sections.get(args.name);
        if (source === undefined)
          throw new ToolFailure('Unknown section name.');
        const offset = Math.min(args.offset ?? 0, source.length);
        const limit = Math.min(args.limit ?? MAX_READ_CHARS, MAX_READ_CHARS);
        const chunk = source.slice(offset, offset + limit);
        return text(
          JSON.stringify({
            name: args.name,
            offset,
            end: offset + chunk.length,
            total: source.length,
            text: chunk,
          }),
        );
      },
    },
    {
      name: 'find_span',
      label: 'Find text',
      description:
        'Case-insensitive literal search across saved sections. Each match has start/end offsets you can cite as evidence {field, start, end}.',
      parameters: Type.Object(
        {
          query: Type.String({ minLength: 1, maxLength: 200 }),
          section: Type.Optional(Type.String({ minLength: 1, maxLength: 120 })),
        },
        { additionalProperties: false },
      ),
      execute: async (_id, params) => {
        const args = params as { query: string; section?: string };
        if (args.section !== undefined && !sections.has(args.section))
          throw new ToolFailure('Unknown section name.');
        const needle = args.query.toLowerCase();
        const matches: object[] = [];
        for (const [name, source] of sections) {
          if (args.section !== undefined && name !== args.section) continue;
          const haystack = source.toLowerCase();
          let index = haystack.indexOf(needle);
          while (index !== -1 && matches.length < MAX_MATCHES) {
            const start = Math.max(0, index - SNIPPET);
            const end = Math.min(
              source.length,
              index + needle.length + SNIPPET,
            );
            matches.push({
              section: name,
              offset: index,
              start: index,
              end: index + needle.length,
              match: source.slice(index, index + needle.length),
              snippet: source.slice(start, end),
            });
            index = haystack.indexOf(needle, index + needle.length);
          }
          if (matches.length >= MAX_MATCHES) break;
        }
        return text(JSON.stringify({ matches }));
      },
    },
  ];
  if (item.kind === 'extraction')
    tools.push({
      name: 'check_pay',
      label: 'Check pay',
      description:
        'Deterministically parse one short pay expression (for example a Compensation sentence) into structured bounds, currency and period.',
      parameters: Type.Object(
        { text: Type.String({ minLength: 1, maxLength: 1024 }) },
        { additionalProperties: false },
      ),
      execute: async (_id, params) => {
        const args = params as { text: string };
        const result = await rpc.call('tool.check_pay', {
          item_id: item.itemId,
          text: args.text,
        });
        return text(JSON.stringify(result));
      },
    });
  const submitName =
    item.kind === 'extraction' ? 'submit_extraction' : 'submit_country';
  tools.push({
    name: submitName,
    label: 'Submit',
    description:
      item.kind === 'extraction'
        ? 'Submit the final source-grounded extraction. Ends the task when accepted.'
        : 'Submit the final work-country prediction. Ends the task when accepted.',
    parameters: Type.Unsafe<JsonObject>(item.submitSchema),
    executionMode: 'sequential',
    execute: async (_id, params) => {
      if (submission.payload !== null)
        return { ...text('Already accepted.'), terminate: true };
      submission.attempts += 1;
      const payload = params as JsonObject;
      const result = await rpc.call('submission.check', {
        item_id: item.itemId,
        payload,
      });
      if (result.accepted === true) {
        submission.payload = payload;
        return { ...text('Accepted.'), terminate: true };
      }
      if (submission.attempts >= MAX_SUBMISSIONS) {
        submission.rejected = true;
        return {
          ...text('Rejected; no attempts remain.'),
          isError: true,
          terminate: true,
        };
      }
      const hint =
        typeof result.hint === 'string' ? result.hint : 'Submission rejected.';
      return { ...text(hint), isError: true };
    },
  });
  return tools;
}
