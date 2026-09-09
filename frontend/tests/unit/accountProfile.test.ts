import { describe, expect, it } from 'vitest';
import {
  googlePhotoUrl,
  parseAccountProfile,
} from '../../src/lib/accountProfile';

describe('account profile field primitives', () => {
  it('keeps absent display fields absent and excludes unrelated fields', () => {
    expect(parseAccountProfile({})).toEqual({
      display_name: null,
      email: null,
      photo_url: null,
    });
    expect(parseAccountProfile({ uid: 'ignored', role: 'admin' })).toEqual(
      parseAccountProfile({}),
    );
    expect(
      parseAccountProfile({ display_name: ' A ', email: ' ' }).display_name,
    ).toBe('A');
    expect(
      parseAccountProfile({ display_name: 1, email: false }).email,
    ).toBeNull();
    expect(() => parseAccountProfile(null)).toThrow(
      'Account profile unavailable.',
    );
  });

  it.each([
    null,
    '',
    'http://lh3.googleusercontent.com/a',
    'https://evil.invalid/a',
    'https://lh3.googleusercontent.com.evil.invalid/a',
    'https://user@lh3.googleusercontent.com/a',
    'https://lh3.googleusercontent.com:8443/a',
    'https://lh3.googleusercontent.com/a#fragment',
    'https://lh3.googleusercontent.com/a\n',
    'https://lh3.googleusercontent.com\\@evil.invalid/a',
    'data:image/png;base64,',
  ])('rejects untrusted photo primitive %s', (value) => {
    expect(googlePhotoUrl(value)).toBeNull();
  });

  it('accepts the exact trusted HTTPS avatar host', () => {
    expect(googlePhotoUrl('https://lh3.googleusercontent.com/a')).toBe(
      'https://lh3.googleusercontent.com/a',
    );
  });
});
