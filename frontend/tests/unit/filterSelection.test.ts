import { describe, expect, it } from 'vitest';
import { matchesSelection } from '../../src/lib/filterSelection';

// Generic strings, not posting fixtures. Real-data browser checks cover combined filters.
describe('filter selections', () => {
  it('matches any selected value, with an empty selection meaning all', () => {
    expect(matchesSelection('beta', ['alpha', 'BETA'])).toBe(true);
    expect(matchesSelection('gamma', ['alpha', 'BETA'])).toBe(false);
    expect(matchesSelection(null, ['alpha'])).toBe(false);
    expect(matchesSelection(null, [])).toBe(true);
  });
});
