import { readFileSync } from 'node:fs';
import { expect, it } from 'vitest';

it('uses the shared header brand asset as the website favicon', () => {
  const html = readFileSync('index.html', 'utf8');
  expect(html).toContain(
    '<link rel="icon" type="image/svg+xml" href="/brand.svg" />',
  );
  expect(readFileSync('public/brand.svg', 'utf8')).toContain('<svg');
});
