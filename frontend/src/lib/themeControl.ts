import { createContext } from 'react';

export type ThemeChoice = 'system' | 'light' | 'dark';

export type ThemeControl = {
  theme: string;
  setTheme: (theme: ThemeChoice) => void;
};

/** Lets the phone account menu offer the workspace's theme choice. */
export const ThemeControlContext = createContext<ThemeControl | null>(null);

export function isThemeChoice(value: unknown): value is ThemeChoice {
  return value === 'system' || value === 'light' || value === 'dark';
}
