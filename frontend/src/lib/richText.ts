import type { components } from './api.generated';
import { safeExternalUrl } from './safeExternalUrl';

export type RichBlock =
  components['schemas']['Paragraph'] | components['schemas']['ListBlock'];
export type RichInline = components['schemas']['Paragraph']['content'][number];
export type RichFormatting = components['schemas']['StoredPostingFormatting'];

const MAX_DEPTH = 4;
const MAX_NODES = 5000;
const MAILTO = /^mailto:[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,253}$/;

/** Defense in depth: links render only for absolute http(s) or plain mailto. */
export function safeRichHref(value: unknown): string | null {
  if (typeof value === 'string' && value.length <= 2048 && MAILTO.test(value))
    return value;
  return typeof value === 'string' && value.length <= 2048
    ? safeExternalUrl(value)
    : null;
}

const record = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);
const flag = (value: unknown) =>
  value === undefined || typeof value === 'boolean';
const text = (value: unknown) =>
  typeof value === 'string' && value.length > 0 && value.length <= 200_000;

function validInline(value: unknown): boolean {
  if (!record(value)) return false;
  const keys = Object.keys(value);
  if (value.type === 'break') return keys.length === 1;
  if (
    !text(value.text) ||
    !flag(value.bold) ||
    !flag(value.italic) ||
    !keys.every((key) =>
      ['type', 'text', 'bold', 'italic', 'href'].includes(key),
    )
  )
    return false;
  if (value.type === 'text') return !('href' in value);
  return value.type === 'link' && safeRichHref(value.href) === value.href;
}

function validBlocks(value: unknown, depth: number, budget: { left: number }) {
  if (!Array.isArray(value) || value.length === 0) return false;
  return value.every((block: unknown): boolean => {
    budget.left -= 1;
    if (!record(block) || budget.left < 0) return false;
    if (block.type === 'paragraph') {
      if (
        Object.keys(block).length !== 2 ||
        !Array.isArray(block.content) ||
        block.content.length === 0
      )
        return false;
      budget.left -= block.content.length;
      return budget.left >= 0 && block.content.every(validInline);
    }
    return (
      block.type === 'list' &&
      Object.keys(block).length === 3 &&
      typeof block.ordered === 'boolean' &&
      depth < MAX_DEPTH &&
      Array.isArray(block.items) &&
      block.items.length > 0 &&
      block.items.every((item: unknown) => validBlocks(item, depth + 1, budget))
    );
  });
}

/**
 * Formatting for labels present in the saved plain-text content, or null.
 * Anything unexpected drops all formatting so the plain text is shown.
 */
export function parseFormatting(
  value: unknown,
  labels: ReadonlySet<string>,
): RichFormatting | null {
  if (value === null || value === undefined) return null;
  if (
    !record(value) ||
    value.version !== 'rich-v1' ||
    !record(value.fields) ||
    Object.keys(value).length !== 2
  )
    return null;
  const entries = Object.entries(value.fields);
  if (
    entries.length === 0 ||
    !entries.every(
      ([label, blocks]) =>
        labels.has(label) && validBlocks(blocks, 0, { left: MAX_NODES }),
    )
  )
    return null;
  return value as RichFormatting;
}
