import { readFileSync } from 'node:fs';
import { expect, it } from 'vitest';

it('applies a local static texture only to the native page background', () => {
  const css = readFileSync('src/styles/index.css', 'utf8');
  expect(css).toMatch(
    /body\s*\{[^}]*background-image:\s*url\('\/paper-grain.svg'\)/,
  );
  expect(css).toMatch(/body\s*\{[^}]*background-size:\s*256px 256px/);
  const texture = readFileSync('public/paper-grain.svg', 'utf8');
  expect(texture).toContain('stitchTiles="stitch"');
  expect(texture).toContain('filterUnits="userSpaceOnUse"');
  expect(texture).toContain(
    '<feTile x="-192" y="-192" width="576" height="576"',
  );
  expect(texture.indexOf('<feTile')).toBeLessThan(
    texture.indexOf('<feGaussianBlur'),
  );
  // The 192px tile renders at 256px: 0.375 SVG units = 0.5 CSS px.
  expect(texture).toContain('<feGaussianBlur stdDeviation="0.375"');
  expect(texture).toContain('<feFuncA type="linear" slope="0.12"');
  expect(texture).toContain('<feFuncR type="linear" slope="3" intercept="-1"');
  expect(texture).not.toMatch(/<animate|<script|<image/);
});
