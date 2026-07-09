// auth.ts — dual-mode auth. Mock header (X-Mock-User-ID) for local/demo; Entra ID
// (MSAL Browser) for production. getAuthHeaders() returns the right header per mode.
import type {
  AuthenticationResult,
  PopupRequest,
  PublicClientApplication as PublicClientApplicationType,
  SilentRequest,
} from '@azure/msal-browser';

export interface AppConfig {
  apiBaseUrl: string;
  authMode: 'mock' | 'entra';
  entraTenantId?: string;
  entraClientId?: string;
  entraApiScope?: string;
  buildId?: string;
}

declare global {
  interface Window {
    __APP_CONFIG__?: Partial<AppConfig>;
  }
}

export function getConfig(): AppConfig {
  const c = window.__APP_CONFIG__ ?? {};
  return {
    apiBaseUrl: c.apiBaseUrl || '/api',
    authMode: (c.authMode as 'mock' | 'entra') || 'mock',
    entraTenantId: c.entraTenantId,
    entraClientId: c.entraClientId,
    entraApiScope: c.entraApiScope,
    buildId: c.buildId,
  };
}

const MOCK_KEY = 'mockUserId';

export function getMockUserId(): string {
  const params = new URLSearchParams(window.location.search);
  const fromQuery = params.get('mockUser');
  if (fromQuery) {
    localStorage.setItem(MOCK_KEY, fromQuery);
    return fromQuery;
  }
  return localStorage.getItem(MOCK_KEY) || 'user-alice';
}

export function setMockUserId(id: string): void {
  localStorage.setItem(MOCK_KEY, id);
}

// ------------------------------------------------------------------ Entra / MSAL
let _msal: PublicClientApplicationType | null = null;
let _msalInit: Promise<PublicClientApplicationType> | null = null;

function scopes(): string[] {
  const s = getConfig().entraApiScope;
  return s ? [s] : [];
}

async function getMsal(): Promise<PublicClientApplicationType> {
  if (_msal) return _msal;
  if (_msalInit) return _msalInit;
  _msalInit = (async () => {
    const cfg = getConfig();
    if (!cfg.entraClientId || !cfg.entraTenantId) {
      throw new Error('Entra auth misconfigured: missing client/tenant id');
    }
    const { PublicClientApplication } = await import('@azure/msal-browser');
    const app = new PublicClientApplication({
      auth: {
        clientId: cfg.entraClientId,
        authority: `https://login.microsoftonline.com/${cfg.entraTenantId}`,
        redirectUri: window.location.origin,
      },
      cache: { cacheLocation: 'localStorage' },
    });
    await app.initialize();
    // Complete any redirect flow; harmless when popup-only.
    await app.handleRedirectPromise().catch(() => undefined);
    _msal = app;
    return app;
  })();
  return _msalInit;
}

/** Acquire an access token, prompting the user via popup if needed. */
async function acquireEntraToken(): Promise<string> {
  const app = await getMsal();
  let account = app.getActiveAccount() ?? app.getAllAccounts()[0] ?? null;

  if (!account) {
    const loginReq: PopupRequest = { scopes: scopes() };
    const result: AuthenticationResult = await app.loginPopup(loginReq);
    account = result.account;
    app.setActiveAccount(account);
    if (result.accessToken) return result.accessToken;
  }

  const silentReq: SilentRequest = { scopes: scopes(), account: account ?? undefined };
  try {
    const result = await app.acquireTokenSilent(silentReq);
    return result.accessToken;
  } catch {
    const result = await app.acquireTokenPopup({ scopes: scopes() });
    if (result.account) app.setActiveAccount(result.account);
    return result.accessToken;
  }
}

/** Interactive sign-out (Entra only). No-op in mock mode. */
export async function signOut(): Promise<void> {
  if (getConfig().authMode !== 'entra') return;
  const app = await getMsal();
  const account = app.getActiveAccount() ?? app.getAllAccounts()[0];
  await app.logoutPopup({ account: account ?? undefined });
}

/** Return auth headers for API calls. */
export async function getAuthHeaders(): Promise<Record<string, string>> {
  const cfg = getConfig();
  if (cfg.authMode === 'entra') {
    const token = await acquireEntraToken();
    return { Authorization: `Bearer ${token}` };
  }
  return { 'X-Mock-User-ID': getMockUserId() };
}
