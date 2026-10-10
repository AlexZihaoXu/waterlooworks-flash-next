import { readFileSync } from 'node:fs';
import { expect, it } from 'vitest';

// JSDOM cannot lay out media queries; the stylesheet contract is checked here
// and the rendered phone geometry by the headless-browser acceptance pass.
it('turns the native Admin settings shell into a full-screen safe-area sheet on phones', () => {
  const css = readFileSync('src/app/AdminSettingsDialog.css', 'utf8');
  const phone = css.slice(css.indexOf('@media (max-width: 40rem)'));
  expect(phone).not.toBe(css);
  const shell = /\.admin-settings-dialog\s*\{([^}]*)\}/.exec(phone)![1];
  expect(shell).toMatch(/width:\s*100%/);
  expect(shell).toMatch(/height:\s*100dvh/);
  expect(shell).toMatch(/max-width:\s*100%/);
  expect(shell).toMatch(/max-height:\s*100dvh/);
  expect(shell).toMatch(/border-radius:\s*0/);
  expect(phone).toMatch(
    /\.admin-settings-dialog-header\s*\{[^}]*env\(safe-area-inset-top\)/,
  );
  expect(phone).toMatch(
    /\.admin-settings-dialog-footer\s*\{[^}]*env\(safe-area-inset-bottom\)/,
  );
  // The selectors stay on the owner-approved native shell, never HeroUI.
  expect(phone).not.toMatch(/\.(tabs|button|card|close-button)\b/);
});
