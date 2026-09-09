import { readFileSync } from 'node:fs';
import { expect, it } from 'vitest';

it('keeps Similar jobs cards free of scores and keyword breakdowns', () => {
  const source = readFileSync('src/app/JobDetail.tsx', 'utf8');
  const sidebar = source.slice(source.indexOf('className="posting-similar"'));
  expect(sidebar).not.toContain('item.score');
  expect(sidebar).not.toContain('Matched / missing');
  expect(sidebar).not.toContain('item[kind]');
  expect(sidebar).toContain('data-similar-posting');
  expect(sidebar).toContain('presentSummary(');
  expect(sidebar).toContain('formatCount(');
});
