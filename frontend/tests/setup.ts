import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterEach } from 'vitest';
import { ResizeObserver } from '@juggle/resize-observer';

// A real DOM API polyfill for JSDOM; no posting data or API responses are mocked.
globalThis.ResizeObserver = ResizeObserver;
// JSDOM has no animation timeline; the real-browser E2E covers animations.
Object.defineProperty(Element.prototype, 'getAnimations', {
  configurable: true,
  value: () => [],
});

// JSDOM has no OS theme. Real-browser checks cover native color-scheme matching.
Object.defineProperty(window, 'matchMedia', {
  configurable: true,
  value: (media: string) =>
    Object.assign(new EventTarget(), {
      media,
      matches: false,
      onchange: null,
      addListener() {},
      removeListener() {},
    }),
});

afterEach(() => {
  cleanup();
  window.history.replaceState(null, '', '/');
  localStorage.removeItem('heroui-theme');
  document.documentElement.classList.remove('light', 'dark');
  document.documentElement.removeAttribute('data-theme');
});
