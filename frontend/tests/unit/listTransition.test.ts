import { readFileSync } from 'node:fs';
import { expect, it } from 'vitest';

const app = readFileSync('src/app/App.tsx', 'utf8');
const styles = readFileSync('src/styles/index.css', 'utf8');

it('crossfades the list skeleton and results together using the detail-page timing', () => {
  const start = app.indexOf('className="results-transition"');
  const end = app.indexOf('{loading ? (', start);
  const transition = app.slice(start, end);
  expect(transition).toContain('<AnimatePresence initial={false} mode="sync">');
  expect(transition).toContain("key={loading ? 'loading' : 'loaded'}");
  expect(transition).toContain('initial={{ opacity: 0 }}');
  expect(transition).toContain('animate={{ opacity: 1 }}');
  expect(transition).toContain('exit={{ opacity: 0 }}');
  expect(transition).toContain('transition={transition}');
  expect(app).toContain('duration: reducedMotion ? 0 : 0.18');
  expect(styles).toMatch(/\.results-layer\s*\{[^}]*grid-area:\s*1\s*\/\s*1/);
});
