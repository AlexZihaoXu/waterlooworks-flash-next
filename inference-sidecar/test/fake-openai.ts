import http from 'node:http';
import type { AddressInfo } from 'node:net';

/** One scripted assistant turn of a local fake OpenAI-compatible server. */
export type ScriptedTurn =
  | { tool: string; args: Record<string, unknown>; usage?: [number, number] }
  | { text: string; usage?: [number, number] }
  | { status: number; body?: string };

export interface RecordedRequest {
  path: string;
  headers: http.IncomingHttpHeaders;
  body: Record<string, unknown>;
}

/**
 * Minimal local stand-in for /v1/chat/completions (streaming) and /v1/models.
 * Binds 127.0.0.1 only; never forwards anything to a real service.
 */
export class FakeOpenAI {
  readonly requests: RecordedRequest[] = [];
  #turns: ScriptedTurn[] = [];
  #server = http.createServer((req, res) => this.#handle(req, res));
  #calls = 0;

  script(turns: ScriptedTurn[]): void {
    this.#turns = [...turns];
  }

  async listen(): Promise<string> {
    await new Promise<void>((resolve) =>
      this.#server.listen(0, '127.0.0.1', resolve),
    );
    const { port } = this.#server.address() as AddressInfo;
    return `http://127.0.0.1:${port}/v1`;
  }

  async close(): Promise<void> {
    this.#server.closeAllConnections();
    await new Promise<void>((resolve) => this.#server.close(() => resolve()));
  }

  #handle(req: http.IncomingMessage, res: http.ServerResponse): void {
    let raw = '';
    req.on('data', (chunk) => (raw += chunk));
    req.on('end', () => {
      const body = raw ? (JSON.parse(raw) as Record<string, unknown>) : {};
      this.requests.push({ path: req.url ?? '', headers: req.headers, body });
      if (req.url === '/v1/models') {
        res.writeHead(200, { 'content-type': 'application/json' });
        res.end(JSON.stringify({ data: [{ id: 'fake-model' }] }));
        return;
      }
      if (req.url !== '/v1/chat/completions') {
        res.writeHead(404);
        res.end();
        return;
      }
      const turn = this.#turns.shift();
      if (!turn) {
        res.writeHead(500, { 'content-type': 'application/json' });
        res.end(JSON.stringify({ error: { message: 'no scripted turn' } }));
        return;
      }
      if ('status' in turn) {
        res.writeHead(turn.status, { 'content-type': 'application/json' });
        res.end(
          turn.body ?? JSON.stringify({ error: { message: 'scripted' } }),
        );
        return;
      }
      this.#calls += 1;
      const id = `chatcmpl-${this.#calls}`;
      const usage = turn.usage ?? [100, 20];
      const chunks: object[] = [];
      if ('tool' in turn) {
        chunks.push({
          id,
          object: 'chat.completion.chunk',
          created: 0,
          model: body.model,
          choices: [
            {
              index: 0,
              delta: {
                role: 'assistant',
                content: null,
                tool_calls: [
                  {
                    index: 0,
                    id: `call_${this.#calls}`,
                    type: 'function',
                    function: {
                      name: turn.tool,
                      arguments: JSON.stringify(turn.args),
                    },
                  },
                ],
              },
              finish_reason: null,
            },
          ],
        });
        chunks.push({
          id,
          object: 'chat.completion.chunk',
          created: 0,
          model: body.model,
          choices: [{ index: 0, delta: {}, finish_reason: 'tool_calls' }],
        });
      } else {
        chunks.push({
          id,
          object: 'chat.completion.chunk',
          created: 0,
          model: body.model,
          choices: [
            {
              index: 0,
              delta: { role: 'assistant', content: turn.text },
              finish_reason: null,
            },
          ],
        });
        chunks.push({
          id,
          object: 'chat.completion.chunk',
          created: 0,
          model: body.model,
          choices: [{ index: 0, delta: {}, finish_reason: 'stop' }],
        });
      }
      chunks.push({
        id,
        object: 'chat.completion.chunk',
        created: 0,
        model: body.model,
        choices: [],
        usage: {
          prompt_tokens: usage[0],
          completion_tokens: usage[1],
          total_tokens: usage[0] + usage[1],
        },
      });
      res.writeHead(200, { 'content-type': 'text/event-stream' });
      for (const chunk of chunks)
        res.write(`data: ${JSON.stringify(chunk)}\n\n`);
      res.end('data: [DONE]\n\n');
    });
  }
}
