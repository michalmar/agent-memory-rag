// Runtime configuration (overridable at deploy time without a rebuild).
// In production the container entrypoint regenerates this file from env vars.
window.__APP_CONFIG__ = {
  apiBaseUrl: '/api',
  authMode: 'mock',
};
