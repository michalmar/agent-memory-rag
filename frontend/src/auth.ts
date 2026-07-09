// auth.ts — mock header auth (fully implemented) + Entra/MSAL (stubbed for the slice).

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

/** Return auth headers for API calls. */
export async function getAuthHeaders(): Promise<Record<string, string>> {
  const cfg = getConfig();
  if (cfg.authMode === 'entra') {
    // MSAL flow lands in P9; the slice runs in mock mode.
    throw new Error('Entra auth not implemented in this build');
  }
  return { 'X-Mock-User-ID': getMockUserId() };
}
