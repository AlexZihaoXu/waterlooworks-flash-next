import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import {
  countryFlagUrl,
  inferredCountryName,
} from '../../src/lib/countryInference';

describe('country location presentation', () => {
  it('uses approved rectangular native images beside location pins, without separate country rows', () => {
    for (const [file, count] of [
      ['App.tsx', 1],
      ['JobDetail.tsx', 2],
    ] as const) {
      const source = readFileSync(`src/app/${file}`, 'utf8');
      expect(source.includes('<Avatar')).toBe(false);
      expect(source).not.toContain('Country (inferred)');
      expect(source).not.toContain(' · inferred');
      expect(source.match(/className="location-field"/g)).toHaveLength(count);
      expect(source.match(/width=\{16\}/g)).toHaveLength(count);
      expect(source).not.toContain('height={12}');
      expect(source.match(/<LuMapPin/g)?.length).toBeGreaterThanOrEqual(count);
    }
    const css = readFileSync('src/styles/index.css', 'utf8');
    expect(css).toContain('.country-flag');
    expect(css).toContain('height: auto;');
    expect(css).toContain('border-radius: 2px;');
    expect(readFileSync('src/lib/workspace.ts', 'utf8')).toContain(
      "country: 'Country'",
    );
  });
  it('uses local rectangular library SVGs, not emoji or remote flags', () => {
    expect(countryFlagUrl('CA')).toContain('ca.svg');
    expect(readFileSync('src/lib/countryInference.ts', 'utf8')).toContain(
      '/flags/4x3/',
    );
    for (const value of ['ZZ', 'UK', 'ca', '../ca', null])
      expect(countryFlagUrl(value)).toBeUndefined();
  });
  it('uses the same native flag styling in the country picker only', () => {
    const source = readFileSync('src/app/FilterOptionList.tsx', 'utf8');
    expect(source).toContain('className="country-flag"');
    expect(source).toContain(
      "row.kind === 'heading' ? undefined : flags?.get(row.value)",
    );
    expect(readFileSync('src/app/App.tsx', 'utf8')).toContain(
      "flags={picker === 'country' ? countryFlags : undefined}",
    );
  });
  it('has no country default', () => {
    expect(inferredCountryName(undefined)).toBeNull();
    expect(countryFlagUrl(null)).toBeUndefined();
  });
});
