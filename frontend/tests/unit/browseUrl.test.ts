import { describe, expect, it } from 'vitest';
import { readBrowseUrl, writeBrowseUrl } from '../../src/lib/browseUrl';

// Generic URL encoding and selection values; no posting/API fixtures.
describe('browse URL', () => {
  it('restores all browsing controls and repeated values', () => {
    expect(
      readBrowseUrl(
        '?q=a%26b&page=3&sort=newest&organization=a&organization=b&city=c&country=d',
      ),
    ).toEqual({
      query: 'a&b',
      page: 3,
      sort: 'newest',
      filters: { organization: ['a', 'b'], location: ['c'], country: ['d'] },
    });
  });
  it('rejects invalid pages/sorts and removes empty or repeated selections', () => {
    for (const page of ['0', '-1', '1.5', 'NaN', '999999999999999999999'])
      expect(readBrowseUrl(`?page=${page}`).page).toBe(1);
    expect(
      readBrowseUrl(
        '?sort=__proto__&organization=&organization=a&organization=a',
      ).sort,
    ).toBe('id');
    expect(
      readBrowseUrl('?organization=&organization=a&organization=a').filters
        .organization,
    ).toEqual(['a']);
  });
  it('round trips encoded values and preserves unrelated parameters and fragments', () => {
    const state = readBrowseUrl(
      '?q=a%26b&page=2&sort=remaining&city=%E2%9C%93&organization=a%2Bb',
    );
    const url = writeBrowseUrl(
      'https://local.invalid/?keep=yes&page=99#section',
      state,
    );
    expect(url).toContain('keep=yes');
    expect(url).toContain('#section');
    expect(readBrowseUrl(new URL(url, 'https://local.invalid').search)).toEqual(
      state,
    );
    expect(
      writeBrowseUrl(
        'https://local.invalid/?q=old&page=8&sort=newest&keep=yes#section',
        readBrowseUrl(''),
      ),
    ).toBe('/?keep=yes#section');
  });
});
