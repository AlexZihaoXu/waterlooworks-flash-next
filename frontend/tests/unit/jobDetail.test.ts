import { describe, expect, it } from 'vitest';
import {
  groupContent,
  jobUrl,
  readJobId,
  shouldOpenJob,
  uniqueSkillMentions,
} from '../../src/lib/jobDetail';

describe('job detail primitives (no posting fixtures)', () => {
  it('preserves all browse parameters and leading zeros', () => {
    const href =
      'http://localhost/?q=engineer&page=2&city=A&city=B&sort=openings&extra=keep#top';
    const detail = jobUrl(href, '0012');
    expect(readJobId(new URL(detail, href).search)).toBe('0012');
    expect(jobUrl(new URL(detail, href).href, null)).toBe(
      new URL(href).pathname + new URL(href).search + '#top',
    );
    expect(readJobId('?job=-1')).toBe('invalid');
    expect(readJobId('?job=')).toBe('invalid');
    expect(readJobId('?job=1%0A')).toBe('invalid');
    expect(readJobId('?job=1&job=2')).toBe('invalid');
    expect(readJobId('')).toBe(null);
  });
  it('only intercepts unmodified primary clicks', () => {
    const primary = {
      button: 0,
      metaKey: false,
      ctrlKey: false,
      altKey: false,
      shiftKey: false,
      defaultPrevented: false,
    };
    expect(shouldOpenJob(primary)).toBe(true);
    for (const key of [
      'metaKey',
      'ctrlKey',
      'altKey',
      'shiftKey',
      'defaultPrevented',
    ])
      expect(shouldOpenJob({ ...primary, [key]: true })).toBe(false);
    expect(shouldOpenJob({ ...primary, button: 1 })).toBe(false);
  });
  it('deduplicates displayed canonical skills without changing source entries', () => {
    const mentions = [
      { name: 'Python', evidence: 'first' },
      { name: 'Python', evidence: 'second' },
      { name: 'SQL', evidence: 'third' },
    ];
    expect(uniqueSkillMentions(mentions)).toEqual([mentions[1], mentions[2]]);
    expect(mentions).toHaveLength(3);
  });
  it('marks control-only targeted content as not captured, not no targets', () => {
    const label = 'Targeted Degrees and Disciplines:';
    expect(
      groupContent({
        [label]: 'Targeted Clusters\nView Targeted Degrees and Disciplines',
      })[0].fields,
    ).toEqual([[label, 'Not captured in saved content']]);
    expect(groupContent({})).toEqual([]);
  });
  it('preserves recorded targeted values while removing heading and control lines', () => {
    const label = 'Targeted Degrees and Disciplines:';
    const value = '- Theme - Computing: Software Development';
    expect(
      groupContent({
        [label]: `Targeted Clusters\nView Targeted Degrees and Disciplines\n${value}`,
      })[0].fields,
    ).toEqual([[label, value]]);
    expect(groupContent({ [label]: value })[0].fields).toEqual([
      [label, value],
    ]);
  });
  it('groups source labels without dropping unassigned labels or inventing values', () => {
    expect(groupContent({})).toEqual([]);
    // Empty strings exercise label routing only, not fabricated posting content.
    const fields = {
      'Job Summary:': '',
      'Application Method:': '',
      'Transportation and Housing:': '',
      'Additional Job Identifiers:': '',
    };
    const groups = groupContent(fields);
    expect(groups.map((group) => group.title)).toEqual([
      'Description and requirements',
      'Work term, location and compensation',
      'Application information',
      'Additional information',
    ]);
    expect(Object.fromEntries(groups.flatMap((group) => group.fields))).toEqual(
      fields,
    );
  });
});
