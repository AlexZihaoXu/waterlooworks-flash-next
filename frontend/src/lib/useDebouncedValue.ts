import { useEffect, useState } from 'react';

export function useDebouncedValue(value: string, delay: number): string {
  const [settled, setSettled] = useState(value);
  // Reset the settled value on clear, including clear-then-type in the same tick.
  if (value === '' && settled !== '') setSettled('');
  useEffect(() => {
    const timer = window.setTimeout(() => setSettled(value), delay);
    return () => window.clearTimeout(timer);
  }, [value, delay]);
  return value === '' ? '' : settled;
}
