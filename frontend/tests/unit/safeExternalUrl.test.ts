import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { safeExternalUrl } from '../../src/lib/safeExternalUrl';

// Pure scheme checks on literal strings; no posting fixtures or API responses.
describe('safeExternalUrl', () => {
  it('keeps absolute http(s) URLs unchanged', () => {
    expect(safeExternalUrl('https://example.com/apply?x=1')).toBe(
      'https://example.com/apply?x=1',
    );
    expect(safeExternalUrl('HTTP://example.com/')).toBe('HTTP://example.com/');
  });
  it.each([
    'javascript:alert(1)',
    'JavaScript:alert(1)',
    ' javascript:alert(1)',
    '\tjavascript:alert(1)',
    'java\nscript:alert(1)',
    'data:text/html,<script>alert(1)</script>',
    'vbscript:msgbox(1)',
    'mailto:someone@example.com',
    '//example.com/apply',
    '/relative/path',
    'example.com/apply',
    ' https://example.com/',
    'https://user:pass@example.com/',
    'https://example.com\\@evil.test/',
    'https://',
    '',
  ])('rejects %j', (value) => {
    expect(safeExternalUrl(value)).toBeNull();
  });
  it.each([null, undefined, 42, {}, ['https://example.com/']])(
    'rejects non-string %j',
    (value) => {
      expect(safeExternalUrl(value)).toBeNull();
    },
  );
  it('guards every external href in the job detail view', () => {
    const source = readFileSync('src/app/JobDetail.tsx', 'utf8');
    expect(source).not.toContain('href={link.url}');
    expect(source).not.toContain('href={analysis.waterlooworks_url}');
    expect(source).toContain('safeExternalUrl(link.url)');
    expect(source).toContain('safeExternalUrl(analysis.waterlooworks_url)');
  });
});
