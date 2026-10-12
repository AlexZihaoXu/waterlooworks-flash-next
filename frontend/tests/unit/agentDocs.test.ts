import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { agentDocsOptions } from '../../src/docs/options';
import config from '../../vite.config';

describe('agent API docs page', () => {
  it('reads only the public agent schema and keeps tokens in memory', () => {
    expect(agentDocsOptions.url).toBe('/api/agent/v1/openapi.json');
    expect(agentDocsOptions.validatorUrl).toBeNull();
    expect(agentDocsOptions.persistAuthorization).toBe(false);
  });

  it('is built as its own page at /docs/', () => {
    const input = (config.build?.rolldownOptions?.input ?? {}) as Record<
      string,
      string
    >;
    expect(
      Object.values(input).some((path) => path.endsWith('docs/index.html')),
    ).toBe(true);
    expect(readFileSync('docs/index.html', 'utf8')).toContain(
      '/src/docs/main.ts',
    );
  });
});
