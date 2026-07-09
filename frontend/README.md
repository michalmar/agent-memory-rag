# Frontend — Vite + Lit + A2UI

Single-page support-chat client. Streams AG-UI events from the backend, renders
Markdown assistant text, and inflates tool results into A2UI surfaces
(shipping-status card, RAG-citations card).

## Requirements

- Node **20+**

## Develop

```bash
npm install
npm run dev      # http://localhost:5175
```

The dev server proxies `/api/*` → `http://localhost:8000` (see `vite.config.ts`),
so start the backend first. Open the app and ask “Where is my order ORD-001?”.

Header controls: mock-user switcher, RAG on/off toggle, light/dark theme toggle.

## Build

```bash
npm run build    # tsc + vite → dist/
npm run preview
```

## Runtime config

`public/config.js` sets `window.__APP_CONFIG__` (`apiBaseUrl`, `authMode`). In a
container deployment the entrypoint regenerates this file from env vars, so the
same image works across environments without a rebuild.

## Structure

```
src/
  app.ts                 <a2ui-native-app> root component (chat UI)
  client.ts              REST + AG-UI SSE parsing
  auth.ts                mock header auth (+ Entra/MSAL stub)
  converters.ts          tool result JSON → A2UI messages
  templates/             verbatim A2UI templates (§B8)
  a2ui/                  A2UI types, processor, <a2ui-surface> renderer
```

## Query-string helpers

- `?mockUser=user-bob` — set the mock user.
- `?debug=1` — verbose console logging.
