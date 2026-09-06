import { describe, expect, it } from 'vitest';
import { logPresentation } from '../../src/lib/logPresentation';

describe('log severity presentation', () => {
  it('uses muted debug, blue information, amber warnings and red failures', () => {
    expect(logPresentation.debug).toEqual({
      color: 'default',
      variant: 'tertiary',
      muted: true,
    });
    expect(logPresentation.info.color).toBe('accent');
    expect(logPresentation.warning.color).toBe('warning');
    expect(logPresentation.error).toEqual({
      color: 'danger',
      variant: 'soft',
      muted: false,
    });
    expect(logPresentation.critical).toEqual({
      color: 'danger',
      variant: 'primary',
      muted: false,
    });
  });
});
