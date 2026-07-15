# Frontend — Memory Thread (Vite + Lit + A2UI)

Memory Thread is the single-page grounded-support workspace. It streams AG-UI
events from the backend, renders Markdown assistant text, and inflates tool
results into A2UI surfaces (shipping-status and citation cards).

## Requirements

- Node **20+**

## Develop

```bash
npm install
npm run dev      # http://localhost:5175
```

The dev server proxies `/api/*` → `http://localhost:8000` (see `vite.config.ts`),
so start the backend first. Open the app and ask “Where is my order ORD-001?”.

Workspace controls cover thread history, immutable runtime selection, fixed
Foundry IQ grounding status, semantic memory, mock identity, profile memory, and
light/dark themes.

In Entra mode, the application validates a cached MSAL session before loading any
user-scoped data. An unauthenticated user sees only the dedicated sign-in screen;
the workspace is hydrated after explicit Microsoft Entra ID sign-in. Expired
sessions return to that screen instead of opening an unexpected authentication
popup from an API request. Mock mode continues to load directly for local demos.

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
  auth.ts                mock auth + explicit Entra/MSAL session lifecycle
  converters.ts          tool result JSON → A2UI messages
  templates/             verbatim A2UI templates (§B8)
  a2ui/                  A2UI types, processor, <a2ui-surface> renderer
```

## Query-string helpers

- `?mockUser=user-bob` — set the mock user.
- `?debug=1` — verbose console logging.
