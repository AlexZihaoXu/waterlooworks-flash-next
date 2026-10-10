export type ThemeChoice = 'system' | 'light' | 'dark';

export type ThemeControl = {
  theme: string;
  setTheme: (theme: ThemeChoice) => void;
};

export function isThemeChoice(value: unknown): value is ThemeChoice {
  return value === 'system' || value === 'light' || value === 'dark';
}
