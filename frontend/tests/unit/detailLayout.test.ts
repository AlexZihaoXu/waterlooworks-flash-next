import { readFileSync } from 'node:fs';
import { expect, it } from 'vitest';

const styles = readFileSync('src/styles/index.css', 'utf8');
const app = readFileSync('src/app/App.tsx', 'utf8');

it('crossfades posting states in the same grid cell without sequential waiting', () => {
  expect(styles).toMatch(/\.detail-state\s*\{[^}]*grid-area:\s*1\s*\/\s*1/);
  expect(app).toContain('key={detail.status}');
  expect(app).toContain('<AnimatePresence initial={false} mode="sync">');
  expect(app).toMatch(
    /className="detail-state"[\s\S]*?initial=\{\{ opacity: 0 \}\}[\s\S]*?animate=\{\{ opacity: 1 \}\}[\s\S]*?exit=\{\{ opacity: 0 \}\}/,
  );
  expect(app).toContain('duration: reducedMotion ? 0 : 0.18');
});

it('constrains the native detail grid rather than clipping wide HeroUI content', () => {
  expect(styles).toMatch(
    /\.detail-scroll\s*>\s*\.stack\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)/,
  );
});
