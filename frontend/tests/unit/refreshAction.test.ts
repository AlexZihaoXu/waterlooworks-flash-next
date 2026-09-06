import { describe, expect, it } from 'vitest';
import { refreshAction } from '../../src/lib/refreshAction';

// Generic control decisions, not fabricated API data.
describe('refresh click decisions', () => {
  it('reports cooldown instead of requesting a refresh', () => {
    expect(refreshAction(true, 120)).toBe('cooldown');
    expect(refreshAction(true, 1)).toBe('cooldown');
    expect(refreshAction(true, 0)).toBe('request');
  });
  it('explains an already-running refresh before cooldown', () => {
    expect(refreshAction(true, 0, true)).toBe('running');
    expect(refreshAction(true, 120, true)).toBe('running');
  });
  it('preserves offline and in-flight guards before cooldown', () => {
    expect(refreshAction(false, 120)).toBe('ignore');
    expect(refreshAction(false, 0)).toBe('ignore');
  });
});
