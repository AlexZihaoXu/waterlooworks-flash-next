import { readFileSync } from 'node:fs';
import { describe, it, expect } from 'vitest';

describe('source-backed location chips', () => {
  it('places documented small chips next to all three location compositions', () => {
    for (const [file, count] of [
      ['App.tsx', 1],
      ['JobDetail.tsx', 2],
    ] as const) {
      const source = readFileSync(`src/app/${file}`, 'utf8');
      expect(
        source.match(/className="location-with-arrangement"/g) ?? [],
      ).toHaveLength(count);
      expect(source).toContain('work_arrangement');
    }
    const detail = readFileSync('src/app/JobDetail.tsx', 'utf8');
    expect(detail).not.toContain('analysis.arrangement?.value');
    expect(readFileSync('src/styles/index.css', 'utf8')).toContain(
      '.location-with-arrangement',
    );
  });
});
