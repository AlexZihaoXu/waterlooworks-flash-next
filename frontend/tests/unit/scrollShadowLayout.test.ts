import { readFileSync } from 'node:fs';
import { expect, it } from 'vitest';

const source = readFileSync('src/app/JobDetail.tsx', 'utf8');
it('uses three unstyled HeroUI scroll regions with hidden scrollbars and keyboard access', () => {
  const regions = (source.match(/<ScrollShadow\b[^>]*>/g) ?? []).filter(
    (region) => !region.includes('isEnabled={false}'),
  );
  expect(regions).toHaveLength(3);
  for (const region of regions) {
    expect(region).toContain('hideScrollBar');
    expect(region).toContain('tabIndex={0}');
    expect(region).toContain('aria-label=');
    expect(region).not.toMatch(/className=|style=/);
  }
});
