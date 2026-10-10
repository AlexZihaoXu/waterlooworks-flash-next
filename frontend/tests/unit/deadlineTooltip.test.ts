import { readFileSync } from 'node:fs';
import { expect, it } from 'vitest';
import { exactDeadline, parseDeadline } from '../../src/lib/deadlines';

// Literal deadline strings only; no posting fixtures.
it('describes the exact deadline in Eastern Time or says it is unverified', () => {
  const raw = 'Jan 15, 2026 09:00 AM';
  expect(exactDeadline(parseDeadline(raw), raw)).toBe(
    'Jan 15, 2026 09:00 AM Eastern Time',
  );
  expect(exactDeadline(null, 'soon')).toBe('Deadline could not be verified');
});

it('exposes the exact deadline through a focusable HeroUI Tooltip, not a hover-only title', () => {
  const source = readFileSync('src/app/App.tsx', 'utf8');
  expect(source).not.toMatch(/\btitle=\{\s*deadline === null/);
  expect(source).toContain('<Tooltip.Trigger');
  expect(source).toContain('exactDeadline(');
  const trigger = source.match(/<Tooltip\.Trigger[^>]*>/)?.[0] ?? '';
  // Label-in-name: the visible badge text starts the accessible name.
  expect(trigger).toMatch(/aria-label=\{`\$\{badge\.label\}/);
  expect(trigger).not.toMatch(/className=|style=/);
});
