// Plaintext is tab-memory only, not cryptographic zeroization of browser memory.
export const REVEAL_LIFETIME_MS = 30_000;
export const SECRET_MASK = '••••••••';

// Unlike bounded imports/HTTP work, human-interactive verification has no
// arbitrary 15-second deadline. Its owning challenge/generation cancels it.
export function waitForInteractiveVerification<T>(
  pending: Promise<T>,
  signal: AbortSignal,
): Promise<T> {
  return new Promise((resolve, reject) => {
    const cancel = () => reject(new Error('Verification cancelled.'));
    signal.addEventListener('abort', cancel, { once: true });
    if (signal.aborted) cancel();
    pending
      .then(
        (value) => {
          if (signal.aborted) cancel();
          else resolve(value);
        },
        () => reject(new Error('Verification was not completed.')),
      )
      .finally(() => signal.removeEventListener('abort', cancel));
  });
}

export function scheduleRevealHide(hide: () => void) {
  const timer = setTimeout(hide, REVEAL_LIFETIME_MS);
  const onVisibility = () => {
    if (document.visibilityState === 'hidden') hide();
  };
  document.addEventListener('visibilitychange', onVisibility);
  return () => {
    clearTimeout(timer);
    document.removeEventListener('visibilitychange', onVisibility);
  };
}
