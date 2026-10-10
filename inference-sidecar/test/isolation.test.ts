import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import http from 'node:http';
import type { AddressInfo } from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { test } from 'node:test';
import {
  guardedFetch,
  installDnsPolicy,
  NetworkBlocked,
} from '../src/network.js';
import { MAIN, ROOT } from './harness.js';

async function server(handler: http.RequestListener) {
  const instance = http.createServer(handler);
  await new Promise<void>((resolve) =>
    instance.listen(0, '127.0.0.1', resolve),
  );
  const { port } = instance.address() as AddressInfo;
  return {
    origin: `http://127.0.0.1:${port}`,
    close: () =>
      new Promise<void>((resolve) => {
        instance.closeAllConnections();
        instance.close(() => resolve());
      }),
  };
}

test('guarded fetch allows only exact origins, refuses redirects and caps bodies', async () => {
  const local = await server((req, res) => {
    if (req.url === '/redirect') {
      res.writeHead(302, { location: 'http://169.254.169.254/latest' });
      res.end();
    } else if (req.url === '/big') {
      res.writeHead(200);
      res.end('x'.repeat(4096));
    } else {
      res.end('ok');
    }
  });
  try {
    const guarded = guardedFetch(new Set([local.origin]), fetch, 1024);
    assert.equal(await (await guarded(`${local.origin}/`)).text(), 'ok');
    await assert.rejects(guarded('http://127.0.0.2:1/'), NetworkBlocked);
    await assert.rejects(
      guarded(`${local.origin.replace('http://', 'http://u:p@')}/`),
      NetworkBlocked,
    );
    await assert.rejects(guarded(`${local.origin}/redirect`));
    await assert.rejects((await guarded(`${local.origin}/big`)).text());
  } finally {
    await local.close();
  }
});

test('DNS policy pins the validated address and blocks other names', async () => {
  const local = await server((req, res) => res.end(req.headers.host));
  try {
    const port = new URL(local.origin).port;
    installDnsPolicy(new Set(), new Map([['model.internal', '127.0.0.1']]));
    const response = await fetch(`http://model.internal:${port}/`);
    assert.equal(await response.text(), `model.internal:${port}`);
    await assert.rejects(fetch(`http://rebind.example:${port}/`));
  } finally {
    await local.close();
  }
});

test('the permission model blocks file writes and child processes', () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'sidecar-perm-'));
  try {
    const script = [
      "const fs = require('node:fs');",
      "const cp = require('node:child_process');",
      'const out = {};',
      `try { fs.writeFileSync(${JSON.stringify(path.join(home, 'x'))}, 'x'); out.write = 'allowed'; } catch (e) { out.write = e.code; }`,
      "try { cp.spawnSync('true'); out.spawn = 'allowed'; } catch (e) { out.spawn = e.code; }",
      "try { new (require('node:worker_threads').Worker)('1', { eval: true }); out.worker = 'allowed'; } catch (e) { out.worker = e.code; }",
      'process.stdout.write(JSON.stringify(out));',
    ].join('\n');
    const result = spawnSync(
      process.execPath,
      ['--permission', `--allow-fs-read=${ROOT}`, '-e', script],
      { env: { HOME: home }, encoding: 'utf8' },
    );
    const out = JSON.parse(result.stdout) as Record<string, string>;
    assert.equal(out.write, 'ERR_ACCESS_DENIED');
    assert.equal(out.spawn, 'ERR_ACCESS_DENIED');
    assert.equal(out.worker, 'ERR_ACCESS_DENIED');
    assert.deepEqual(fs.readdirSync(home), []);
  } finally {
    fs.rmSync(home, { recursive: true, force: true });
  }
});

test('catalog lists pi-ai models offline under the permission model', () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'sidecar-cat-'));
  try {
    const result = spawnSync(
      process.execPath,
      [
        '--permission',
        `--allow-fs-read=${ROOT}`,
        MAIN,
        'catalog',
        'openai-codex',
      ],
      { env: { HOME: home }, encoding: 'utf8', cwd: home },
    );
    assert.equal(result.status, 0, result.stderr);
    const models = (
      JSON.parse(result.stdout) as {
        models: { id: string; efforts: string[] }[];
      }
    ).models;
    assert.ok(models.length > 0);
    for (const model of models) assert.ok(Array.isArray(model.efforts));
    assert.deepEqual(fs.readdirSync(home), []);
    const bad = spawnSync(process.execPath, [MAIN, 'catalog', 'anthropic'], {
      env: { HOME: home },
      encoding: 'utf8',
    });
    assert.equal(bad.status, 2);
  } finally {
    fs.rmSync(home, { recursive: true, force: true });
  }
});
