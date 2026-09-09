import type { components } from './api.generated';

export type AccountProfile = components['schemas']['AccountProfile'];

// Match the server's narrow Google avatar host allowlist before URL normalization.
export function googlePhotoUrl(value: unknown): string | null {
  if (
    typeof value !== 'string' ||
    value.length > 2048 ||
    !/^https:\/\/lh[3-6]\.googleusercontent\.com\/[^\s\\#]*$/.test(value)
  )
    return null;
  return value;
}

function displayText(value: unknown): string | null {
  if (typeof value !== 'string' || value.length > 320) return null;
  const text = value.trim();
  const hasControl = Array.from(text).some((char) => {
    const code = char.charCodeAt(0);
    return code < 32 || code === 127;
  });
  return text && !hasControl ? text : null;
}

export function parseAccountProfile(value: unknown): AccountProfile {
  if (typeof value !== 'object' || value === null || Array.isArray(value))
    throw new Error('Account profile unavailable.');
  const fields = value as Record<string, unknown>;
  return {
    display_name: displayText(fields.display_name),
    email: displayText(fields.email),
    photo_url: googlePhotoUrl(fields.photo_url),
  };
}
