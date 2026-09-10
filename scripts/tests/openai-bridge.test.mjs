import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { encodeBridgeMessage } from '../openai-bridge.mjs';
import { loadEngine } from '../extraction-engine.mjs';
import { loadCountryEngine } from '../country-engine.mjs';
import { compatibleOrigin, validConfiguration } from '../inference-reuse.mjs';

const hash = (text) => createHash('sha256').update(text).digest('hex');

test('bridge messages are newline framed and bounded before process I/O', () => {
  assert.deepEqual(JSON.parse(encodeBridgeMessage({ id: 0, op: 'bind' })), {
    id: 0,
    op: 'bind',
  });
  assert.throws(() =>
    encodeBridgeMessage({ text: 'x'.repeat(2 * 1024 * 1024) }),
  );
});
for (const [kind, load] of [
  ['extraction', loadEngine],
  ['country', loadCountryEngine],
]) {
  test(`${kind} production config is fixed OpenAI without discovery or provider I/O`, async () => {
    const engine = await load({ bridge: {} });
    assert.equal(engine.configuration.model.id, 'gpt-5.6-luna');
    assert.equal(engine.configuration.reasoning, 'low');
    assert.equal(engine.configuration.provider, 'openai-responses');
    assert.equal(
      validConfiguration(
        JSON.stringify(engine.configuration),
        engine.configurationHash,
        kind,
      ),
      true,
    );
    await assert.rejects(load());
    await assert.rejects(load({ bridge: {}, concurrency: 5 }));
  });
  test(`${kind} carry-forward rejects missing/tampered origin and incompatible validators`, async () => {
    const engine = await load({ bridge: {} });
    const raw = JSON.stringify(engine.configuration);
    assert.equal(
      compatibleOrigin(
        { configuration_json: raw, configuration_hash: hash(raw) },
        engine,
        kind,
      ),
      true,
    );
    assert.equal(
      compatibleOrigin(
        { configuration_json: raw, configuration_hash: '0'.repeat(64) },
        engine,
        kind,
      ),
      false,
    );
    assert.equal(compatibleOrigin({}, engine, kind), false);
    const invalid = JSON.stringify({
      ...engine.configuration,
      version: 'obsolete',
    });
    assert.equal(
      compatibleOrigin(
        { configuration_json: invalid, configuration_hash: hash(invalid) },
        engine,
        kind,
      ),
      false,
    );
    const altered = structuredClone(engine.configuration);
    altered.codeHashes = {};
    const changed = JSON.stringify(altered);
    assert.equal(
      compatibleOrigin(
        { configuration_json: changed, configuration_hash: hash(changed) },
        engine,
        kind,
      ),
      false,
    );
  });
}
