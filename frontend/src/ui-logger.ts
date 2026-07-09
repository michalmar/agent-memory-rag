// ui-logger.ts — tiny debug logger toggled by ?debug=1.
const enabled = new URLSearchParams(window.location.search).has('debug');

export const uiLogger = {
  log(...args: unknown[]): void {
    if (enabled) console.log('[a2ui]', ...args);
  },
  error(...args: unknown[]): void {
    console.error('[a2ui]', ...args);
  },
};
