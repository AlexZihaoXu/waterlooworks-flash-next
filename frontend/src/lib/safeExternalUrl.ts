// Defense in depth: the backend parser already rejects unsafe schemes, but any
// href rendered in the browser must independently be an absolute http(s) URL.
export function safeExternalUrl(value: unknown): string | null {
  if (typeof value !== 'string' || !/^https?:\/\/[^\s\\]+$/i.test(value))
    return null;
  try {
    const url = new URL(value);
    if (
      (url.protocol !== 'http:' && url.protocol !== 'https:') ||
      !url.hostname ||
      url.username ||
      url.password
    )
      return null;
  } catch {
    return null;
  }
  return value;
}
