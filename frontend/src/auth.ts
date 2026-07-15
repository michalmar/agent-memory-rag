// auth.ts — dual-mode auth. Mock header (X-Mock-User-ID) for local/demo; Entra ID
// (MSAL Browser) for production. getAuthHeaders() returns the right header per mode.
import type {
  AccountInfo,
  PopupRequest,
  PublicClientApplication as PublicClientApplicationType,
  SilentRequest,
} from '@azure/msal-browser';
import { InteractionRequiredAuthError } from '@azure/msal-browser';

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
export const AUTH_REQUIRED_EVENT = 'memory-thread:auth-required';

export interface AuthSession {
  displayName: string;
  username: string;
}

export class AuthRequiredError extends Error {
  constructor() {
    super('An interactive sign-in is required');
    this.name = 'AuthRequiredError';
  }
}

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
    _msal = app;
    return app;
  })();
  try {
    return await _msalInit;
  } catch (error) {
    _msalInit = null;
    throw error;
  }
}

function findAccount(app: PublicClientApplicationType): AccountInfo | null {
  const activeAccount = app.getActiveAccount();
  if (activeAccount) return activeAccount;

  const accounts = app.getAllAccounts();
  const account = accounts.length === 1 ? accounts[0] : null;
  if (account) app.setActiveAccount(account);
  return account;
}

function sessionFromAccount(account: AccountInfo): AuthSession {
  return {
    displayName: account.name || account.username,
    username: account.username,
  };
}

function tokenRequest(account: AccountInfo): SilentRequest {
  return { scopes: scopes(), account };
}

/** Validate a cached Entra session without opening interactive UI. */
export async function initializeAuthSession(): Promise<AuthSession | null> {
  if (getConfig().authMode !== 'entra') {
    const username = getMockUserId();
    return { displayName: username, username };
  }

  const app = await getMsal();
  const account = findAccount(app);
  if (!account) return null;

  try {
    await app.acquireTokenSilent(tokenRequest(account));
    return sessionFromAccount(account);
  } catch (error) {
    if (error instanceof InteractionRequiredAuthError) {
      app.setActiveAccount(null);
      return null;
    }
    throw error;
  }
}

/** Start the only interactive Entra sign-in flow. */
export async function signIn(): Promise<AuthSession> {
  if (getConfig().authMode !== 'entra') {
    const username = getMockUserId();
    return { displayName: username, username };
  }

  const app = await getMsal();
  const loginReq: PopupRequest = {
    scopes: scopes(),
    ...(app.getAllAccounts().length > 1 ? { prompt: 'select_account' } : {}),
  };
  const result = await app.loginPopup(loginReq);
  if (!result.account) throw new Error('Entra sign-in completed without an account');
  app.setActiveAccount(result.account);
  return sessionFromAccount(result.account);
}

/** Acquire an access token without unexpectedly opening interactive UI. */
async function acquireEntraToken(): Promise<string> {
  const app = await getMsal();
  const account = findAccount(app);
  if (!account) {
    window.dispatchEvent(new Event(AUTH_REQUIRED_EVENT));
    throw new AuthRequiredError();
  }
  try {
    const result = await app.acquireTokenSilent(tokenRequest(account));
    return result.accessToken;
  } catch (error) {
    if (error instanceof InteractionRequiredAuthError) {
      window.dispatchEvent(new Event(AUTH_REQUIRED_EVENT));
      throw new AuthRequiredError();
    }
    throw error;
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
