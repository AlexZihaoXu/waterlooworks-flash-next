import assert from 'node:assert/strict';
import { after, before, test } from 'node:test';
import { FakeOpenAI } from './fake-openai.js';
import { backend, itemMessage, runSession, startMessage } from './harness.js';

const fake = new FakeOpenAI();
let baseUrl = '';

before(async () => {
  baseUrl = await fake.listen();
});
after(async () => {
  await fake.close();
});

const SUBMISSION = { base_rates: [], skills: [] };

function result(messages: Record<string, unknown>[]) {
  return messages.find((message) => message.type === 'item_result');
}

test('tool loop reads sections, submits, ends, and leaves HOME empty', async () => {
  fake.requests.length = 0;
  fake.script([
    { tool: 'list_sections', args: {}, usage: [120, 10] },
    {
      tool: 'read_section',
      args: { name: 'Required Skills:' },
      usage: [150, 12],
    },
    { tool: 'find_span', args: { query: 'python' }, usage: [160, 12] },
    { tool: 'submit_extraction', args: SUBMISSION, usage: [170, 30] },
    { text: 'should never be requested' },
  ]);
  const session = await runSession(
    [startMessage(baseUrl), itemMessage('item-1'), { type: 'end' }],
    backend(),
  );
  assert.equal(session.code, 0, session.stderr);
  assert.equal(session.stderr, '');
  assert.deepEqual(session.homeEntries, []);
  assert.equal(session.messages[0]?.type, 'ready');
  const outcome = result(session.messages);
  assert.equal(outcome?.status, 'submitted');
  assert.deepEqual(outcome?.payload, SUBMISSION);
  assert.equal(outcome?.turns, 4);
  assert.equal(outcome?.tool_calls, 4);
  assert.equal(session.messages.at(-1)?.type, 'done');
  // One reservation and one finalization per model turn, with reported usage.
  const reserves = session.rpcs.filter(
    (rpc) => rpc.method === 'ledger.reserve',
  );
  const finals = session.rpcs.filter((rpc) => rpc.method === 'ledger.finalize');
  assert.deepEqual(
    reserves.map((rpc) => rpc.params.turn),
    [1, 2, 3, 4],
  );
  assert.equal(finals.length, 4);
  assert.deepEqual(finals[3]?.params.usage, {
    input: 170,
    output: 30,
    cache_read: 0,
    cache_write: 0,
    reasoning: 0,
  });
  assert.equal(finals[0]?.params.outcome, 'completed');
  assert.equal(finals[0]?.params.http_status, 200);
  // Only this item's custom tools are offered; no built-in coding tools.
  const completions = fake.requests.filter(
    (r) => r.path === '/v1/chat/completions',
  );
  assert.equal(completions.length, 4);
  for (const request of completions) {
    const names = (request.body.tools as { function: { name: string } }[]).map(
      (tool) => tool.function.name,
    );
    assert.deepEqual(names.sort(), [
      'check_pay',
      'find_span',
      'list_sections',
      'read_section',
      'submit_extraction',
    ]);
    assert.equal(request.headers.authorization, 'Bearer test-token');
    assert.equal(request.body.max_tokens, 1024);
    assert.equal(request.body.store, undefined);
  }
  // The section text reached the model only through the tool result.
  const lastMessages = JSON.stringify(completions[3]?.body.messages);
  assert.match(lastMessages, /Python is required\./);
});

test('turn cap fails the item without halting the session', async () => {
  fake.script([
    { tool: 'list_sections', args: {} },
    { tool: 'list_sections', args: {} },
    { tool: 'list_sections', args: {} },
  ]);
  const session = await runSession(
    [
      startMessage(baseUrl, { max_turns: 2 }),
      itemMessage('item-2'),
      { type: 'end' },
    ],
    backend(),
  );
  assert.equal(session.code, 0);
  const outcome = result(session.messages);
  assert.equal(outcome?.status, 'failed');
  assert.equal(outcome?.category, 'max_turns');
  assert.equal(
    session.rpcs.filter((rpc) => rpc.method === 'ledger.reserve').length,
    2,
  );
});

test('tool-call cap blocks further tools and fails the item', async () => {
  fake.script([
    { tool: 'list_sections', args: {} },
    { tool: 'list_sections', args: {} },
    { tool: 'list_sections', args: {} },
  ]);
  const session = await runSession(
    [
      startMessage(baseUrl, { max_tool_calls: 1 }),
      itemMessage('item-3'),
      { type: 'end' },
    ],
    backend(),
  );
  const outcome = result(session.messages);
  assert.equal(outcome?.category, 'max_tool_calls');
});

test('repeated rejected submissions end as evidence_rejected', async () => {
  fake.script([
    { tool: 'submit_extraction', args: SUBMISSION },
    { tool: 'submit_extraction', args: SUBMISSION },
    { tool: 'submit_extraction', args: SUBMISSION },
  ]);
  const session = await runSession(
    [startMessage(baseUrl), itemMessage('item-4'), { type: 'end' }],
    backend({
      'submission.check': () => ({
        ok: true,
        result: {
          accepted: false,
          hint: 'Evidence must be an exact substring.',
        },
      }),
    }),
  );
  const outcome = result(session.messages);
  assert.equal(outcome?.status, 'failed');
  assert.equal(outcome?.category, 'evidence_rejected');
});

test('budget refusal stops before any provider request', async () => {
  fake.requests.length = 0;
  fake.script([{ tool: 'list_sections', args: {} }]);
  const session = await runSession(
    [startMessage(baseUrl), itemMessage('item-5'), { type: 'end' }],
    backend({
      'ledger.reserve': () => ({ ok: false, error: 'budget_stopped' }),
    }),
  );
  assert.equal(result(session.messages)?.category, 'budget_stopped');
  assert.equal(fake.requests.length, 0);
});

test('HTTP 429 and 401 map to fixed categories with no retries', async () => {
  fake.requests.length = 0;
  fake.script([{ status: 429 }]);
  let session = await runSession(
    [startMessage(baseUrl), itemMessage('item-6'), { type: 'end' }],
    backend(),
  );
  assert.equal(result(session.messages)?.category, 'rate_limited');
  assert.equal(
    fake.requests.filter((r) => r.path === '/v1/chat/completions').length,
    1,
  );
  const final = session.rpcs.find((rpc) => rpc.method === 'ledger.finalize');
  assert.equal(final?.params.outcome, 'error');
  assert.equal(final?.params.http_status, 429);
  fake.script([
    { status: 401, body: '{"error":{"message":"secret-ish detail"}}' },
  ]);
  session = await runSession(
    [startMessage(baseUrl), itemMessage('item-7'), { type: 'end' }],
    backend(),
  );
  assert.equal(result(session.messages)?.category, 'auth');
  assert.doesNotMatch(session.stdout, /secret-ish detail/);
});

test('unknown start fields are rejected without echoing values', async () => {
  const session = await runSession(
    [{ ...startMessage(baseUrl), surprise: 'do-not-echo-me' }],
    backend(),
  );
  assert.equal(session.code, 1);
  assert.doesNotMatch(session.stdout + session.stderr, /do-not-echo-me/);
  assert.equal(session.messages.length, 0);
});

test('items beyond the configured concurrency are a protocol error', async () => {
  fake.script([
    { tool: 'list_sections', args: {} },
    { tool: 'list_sections', args: {} },
  ]);
  const session = await runSession(
    [startMessage(baseUrl), itemMessage('item-8'), itemMessage('item-9')],
    backend(),
  );
  assert.equal(session.code, 1);
  assert.ok(session.messages.some((m) => m.type === 'fatal'));
});

test('custom reasoning metadata is injected verbatim and off uses disable_with', async () => {
  fake.requests.length = 0;
  fake.script([{ tool: 'submit_extraction', args: SUBMISSION }]);
  const custom = {
    base_url: baseUrl,
    host: '127.0.0.1',
    pinned_address: '127.0.0.1',
    context_window: 32768,
    reasoning_field: 'reasoning_effort',
    disable_with: { reasoning_effort: 'none' },
  };
  await runSession(
    [
      startMessage(baseUrl, { reasoning: 'xhigh', custom }),
      itemMessage('item-10'),
      { type: 'end' },
    ],
    backend(),
  );
  assert.equal(fake.requests.at(-1)?.body.reasoning_effort, 'xhigh');
  fake.script([{ tool: 'submit_extraction', args: SUBMISSION }]);
  await runSession(
    [
      startMessage(baseUrl, { reasoning: 'off', custom }),
      itemMessage('item-11'),
      { type: 'end' },
    ],
    backend(),
  );
  assert.equal(fake.requests.at(-1)?.body.reasoning_effort, 'none');
});

test('a plain-text answer gets one nudge, then fails as no_submission', async () => {
  fake.script([{ text: 'Here is my answer.' }, { text: 'Still prose.' }]);
  const session = await runSession(
    [startMessage(baseUrl), itemMessage('item-12'), { type: 'end' }],
    backend(),
  );
  const outcome = result(session.messages);
  assert.equal(outcome?.category, 'no_submission');
  assert.equal(outcome?.turns, 2);
});

test('requests to any other origin are refused by the network policy', async () => {
  // The model base URL host must match the pinned host; a different origin is
  // unreachable even though it is a valid URL.
  const session = await runSession(
    [
      startMessage(baseUrl, {
        custom: {
          base_url: 'http://localhost:9/v1',
          host: 'localhost',
          pinned_address: '127.0.0.1',
          context_window: 32768,
          reasoning_field: null,
          disable_with: null,
        },
      }),
      itemMessage('item-13'),
      { type: 'end' },
    ],
    backend(),
  );
  const outcome = result(session.messages);
  assert.equal(outcome?.status, 'failed');
  assert.equal(outcome?.category, 'connection');
});

test('submit arguments are validated against the backend schema before the backend sees them', async () => {
  fake.script([
    { tool: 'submit_extraction', args: { base_rates: [], unexpected: true } },
    { tool: 'submit_extraction', args: SUBMISSION },
  ]);
  const session = await runSession(
    [startMessage(baseUrl), itemMessage('item-14'), { type: 'end' }],
    backend(),
  );
  const checks = session.rpcs.filter(
    (rpc) => rpc.method === 'submission.check',
  );
  assert.equal(checks.length, 1);
  assert.deepEqual(checks[0]?.params.payload, SUBMISSION);
  assert.equal(result(session.messages)?.status, 'submitted');
});

test('the final allowed turn forces the submit tool by name', async () => {
  fake.requests.length = 0;
  fake.script([
    { tool: 'list_sections', args: {} },
    { tool: 'submit_extraction', args: SUBMISSION },
  ]);
  const session = await runSession(
    [
      startMessage(baseUrl, { max_turns: 2 }),
      itemMessage('item-15'),
      { type: 'end' },
    ],
    backend(),
  );
  assert.equal(result(session.messages)?.status, 'submitted');
  const completions = fake.requests.filter(
    (r) => r.path === '/v1/chat/completions',
  );
  assert.equal(completions[0]?.body.tool_choice, undefined);
  assert.deepEqual(completions[1]?.body.tool_choice, {
    type: 'function',
    function: { name: 'submit_extraction' },
  });
});
