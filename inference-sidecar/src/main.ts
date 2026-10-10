import { Channel } from './channel.js';
import { RpcCredentialStore } from './credentials.js';
import { guardedFetch, installDnsPolicy } from './network.js';
import type { ItemMessage, StartConfig } from './protocol.js';
import {
  ProviderSetupError,
  catalog,
  createRuntime,
  networkPolicy,
  type Runtime,
} from './providers.js';
import { runItem, type RunnerContext } from './runner.js';

/**
 * Headless, disposable runner. The backend spawns one process per run with a
 * scrubbed environment and an empty private HOME; this process never writes
 * files, keeps everything in memory and exits when the run ends.
 */

function exit(code: number): void {
  process.stdout.write('', () => process.exit(code));
}

function runCatalog(provider: string | undefined): void {
  if (provider !== 'openai' && provider !== 'openai-codex') return exit(2);
  process.stdout.write(
    JSON.stringify({ models: catalog(provider) }) + '\n',
    () => process.exit(0),
  );
}

function runSession(): void {
  let config: StartConfig | null = null;
  let context: RunnerContext | null = null;
  let ending = false;
  let finished = false;
  const active = new Map<string, AbortController>();

  const finish = (code: number) => {
    if (finished) return;
    finished = true;
    for (const controller of active.values()) controller.abort();
    exit(code);
  };

  const fatal = (category: string) => {
    channel.send({ type: 'fatal', category });
    finish(1);
  };

  const maybeDone = () => {
    if (ending && active.size === 0 && !finished) {
      channel.send({ type: 'done' });
      finish(0);
    }
  };

  const start = (next: StartConfig) => {
    if (config !== null) return fatal('protocol');
    config = next;
    const policy = networkPolicy(next);
    installDnsPolicy(policy.hosts, policy.pinned);
    const fetchImpl = guardedFetch(policy.origins);
    // pi-ai OAuth refresh uses the global fetch; it gets the same policy.
    globalThis.fetch = fetchImpl;
    let runtime: Runtime;
    try {
      runtime = createRuntime(next, new RpcCredentialStore(channel));
    } catch (error) {
      return fatal(
        error instanceof ProviderSetupError ? error.code : 'configuration',
      );
    }
    context = { config: next, runtime, rpc: channel, fetchImpl };
    channel.send({
      type: 'ready',
      provider_id: runtime.providerId,
      model_id: runtime.model.id,
    });
  };

  const accept = (item: ItemMessage) => {
    if (context === null || ending) return fatal('protocol');
    if (active.has(item.itemId) || active.size >= context.config.concurrency)
      return fatal('protocol');
    const controller = new AbortController();
    active.set(item.itemId, controller);
    runItem(context, item, controller.signal)
      .then((outcome) => {
        channel.send({
          type: 'item_result',
          item_id: item.itemId,
          status: outcome.status,
          payload: outcome.payload,
          category: outcome.category,
          turns: outcome.turns,
          tool_calls: outcome.toolCalls,
        });
      })
      .catch(() => {
        channel.send({
          type: 'item_result',
          item_id: item.itemId,
          status: 'failed',
          payload: null,
          category: 'internal',
          turns: 0,
          tool_calls: 0,
        });
      })
      .finally(() => {
        active.delete(item.itemId);
        maybeDone();
      });
  };

  const channel = new Channel(process.stdin, process.stdout, {
    onMessage: (message) => {
      if (message.type === 'start') start(message.config);
      else if (message.type === 'item') accept(message.item);
      else {
        ending = true;
        maybeDone();
      }
    },
    // EOF without `end` is a stop: abandon in-flight work and exit.
    onClose: () => finish(ending ? 0 : 1),
  });
}

process.on('uncaughtException', () => {
  process.stdout.write(
    JSON.stringify({ type: 'fatal', category: 'internal' }) + '\n',
  );
  process.exit(1);
});
process.on('unhandledRejection', () => {
  process.stdout.write(
    JSON.stringify({ type: 'fatal', category: 'internal' }) + '\n',
  );
  process.exit(1);
});

const command = process.argv[2];
if (command === 'catalog') runCatalog(process.argv[3]);
else if (command === 'run') runSession();
else exit(2);
