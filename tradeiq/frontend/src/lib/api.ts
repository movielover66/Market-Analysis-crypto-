/**
 * Typed API Client for TRADEIQ Backend (/api/v1).
 * Never stores or transmits broker secrets or Supabase service-role keys.
 */
export type FreshnessClass = 'LIVE' | 'DELAYED' | 'HISTORICAL' | 'SIMULATED' | 'STALE' | 'UNAVAILABLE';

export interface ProvenanceEnvelope {
  source: string;
  exchange_ts_ist: string;
  ingested_ts_utc: string;
  freshness: FreshnessClass;
  quality_status: 'VALID' | 'QUARANTINED' | 'PROVISIONAL';
  is_simulated: boolean;
}

export interface NormalizedInstrument {
  instrument_id: string;
  exchange: string;
  segment: string;
  symbol: string;
  name: string;
  provider_token: string;
  tick_size: string;
  lot_size: number;
  sector: string;
  status: string;
  source: string;
}

export interface SystemStatusResponse {
  project: string;
  release_stage: string;
  exchange: string;
  segment: string;
  trading_modes: string[];
  execution_mode: string;
  live_order_execution: boolean;
  config: {
    app_env: string;
    demo_mode: boolean;
    supabase_configured: boolean;
    angel_one_configured: boolean;
    gemini_configured: boolean;
  };
}

/** Standard response wrapper the dashboard expects (provenance fields + payload). */
export interface Envelope<T = any> {
  data: T;
  source: string;
  freshness: FreshnessClass | string;
  as_of_utc: string;
  market_session: string;
}

/* -------------------------------------------------------------------------------------------
 * Single-owner session handling
 *
 *  - The access token lives in module memory (setAuthToken) and is mirrored to sessionStorage so a
 *    page refresh keeps the owner signed in. localStorage is intentionally NEVER used: sessionStorage
 *    is cleared when the tab closes and is not shared across tabs.
 *  - Nothing here knows the owner password or any token value; both come from the user / the server.
 * ----------------------------------------------------------------------------------------- */
export const SESSION_TOKEN_KEY = 'tradeiq.owner.session';
export const LOGIN_PATH = '/api/v1/auth/login';

let authToken: string | null = null;

export function setAuthToken(token: string | null): void {
  authToken = token;
}

export function getAuthToken(): string | null {
  return authToken;
}

function sessionStore(): Storage | null {
  try {
    return typeof window !== 'undefined' ? window.sessionStorage : null;
  } catch {
    return null; // storage blocked (privacy mode etc.) — session simply won't survive a refresh
  }
}

/** Reads the (unverified) `exp` claim in ms. Used only for UX (auto sign-out); the server is the authority. */
export function getTokenExpiryMs(token: string | null = authToken): number | null {
  if (!token) return null;
  try {
    const part = token.split('.')[1];
    if (!part) return null;
    const b64 = part.replace(/-/g, '+').replace(/_/g, '/');
    const padded = b64 + '='.repeat((4 - (b64.length % 4)) % 4);
    const claims = JSON.parse(atob(padded));
    return typeof claims?.exp === 'number' ? claims.exp * 1000 : null;
  } catch {
    return null;
  }
}

function persistToken(token: string): void {
  try {
    sessionStore()?.setItem(SESSION_TOKEN_KEY, token);
  } catch {
    /* quota / blocked: keep working with the in-memory token */
  }
}

/** Forget the session everywhere (memory + sessionStorage). */
export function clearSession(): void {
  setAuthToken(null);
  try {
    sessionStore()?.removeItem(SESSION_TOKEN_KEY);
  } catch {
    /* ignore */
  }
}

/** Re-arms the in-memory token from sessionStorage after a refresh. Returns true when a live session exists. */
export function restoreSession(): boolean {
  let stored: string | null = null;
  try {
    stored = sessionStore()?.getItem(SESSION_TOKEN_KEY) ?? null;
  } catch {
    stored = null;
  }
  if (!stored) return false;
  const exp = getTokenExpiryMs(stored);
  if (exp !== null && exp <= Date.now()) {
    clearSession();
    return false;
  }
  setAuthToken(stored);
  return true;
}

export class ApiError extends Error {
  status: number;
  code?: string;
  retryAfterSec?: number;
  constructor(message: string, status: number, code?: string, retryAfterSec?: number) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.retryAfterSec = retryAfterSec;
  }
}

export function isUnauthorizedError(err: unknown): boolean {
  return err instanceof ApiError && err.status === 401;
}

type UnauthorizedListener = () => void;
const unauthorizedListeners = new Set<UnauthorizedListener>();

/** Subscribe to "session is no longer valid" events (HTTP 401 on a private call). Returns an unsubscribe fn. */
export function onUnauthorized(listener: UnauthorizedListener): () => void {
  unauthorizedListeners.add(listener);
  return () => {
    unauthorizedListeners.delete(listener);
  };
}

function notifyUnauthorized(): void {
  unauthorizedListeners.forEach((fn) => {
    try {
      fn();
    } catch {
      /* a listener must never break the request pipeline */
    }
  });
}

async function readErrorBody(response: Response): Promise<{ code?: string; message?: string }> {
  const body = await response.json().catch(() => ({}));
  return { code: body?.error?.code, message: body?.error?.message };
}

function parseRetryAfter(response: Response): number | undefined {
  const raw = response.headers.get('Retry-After');
  const n = raw ? parseInt(raw, 10) : NaN;
  return Number.isFinite(n) && n > 0 ? n : undefined;
}

export function formatWait(seconds: number): string {
  if (seconds < 60) return `${seconds} second${seconds === 1 ? '' : 's'}`;
  const mins = Math.ceil(seconds / 60);
  return `${mins} minute${mins === 1 ? '' : 's'}`;
}

/** Maps a failed login response to a message the owner can act on. */
async function loginError(response: Response): Promise<ApiError> {
  const { code, message } = await readErrorBody(response);
  const retry = parseRetryAfter(response);
  switch (response.status) {
    case 401:
      return new ApiError('Incorrect password. Please try again.', 401, code);
    case 422:
      return new ApiError('Enter your owner password to continue.', 422, code);
    case 429:
      return new ApiError(
        retry
          ? `Too many failed attempts. Try again in ${formatWait(retry)}.`
          : 'Too many failed attempts. Please wait a few minutes and try again.',
        429,
        code,
        retry,
      );
    case 503:
      return new ApiError('Owner login is not configured on this server. Check the deployment settings.', 503, code);
    default:
      if (response.status >= 500) {
        return new ApiError('The server hit an error while signing you in. Please try again shortly.', response.status, code);
      }
      return new ApiError(message || `Sign-in failed (HTTP ${response.status}).`, response.status, code);
  }
}

/**
 * Owner login. Sends ONLY {"password": ...}. On success the returned `access_token` is handed to
 * setAuthToken() and mirrored into sessionStorage. A 401 here means "wrong password" and is NOT treated
 * as a session expiry (so it never triggers the global unauthorized handler).
 */
export async function loginOwner(password: string): Promise<{ expiresAtMs: number | null }> {
  let response: Response;
  try {
    response = await fetch(LOGIN_PATH, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password }),
    });
  } catch {
    throw new ApiError('Cannot reach the TRADEIQ server. Check your connection and try again.', 0, 'NETWORK_ERROR');
  }
  if (!response.ok) throw await loginError(response);

  const body = await response.json().catch(() => null);
  const token = body?.access_token;
  if (typeof token !== 'string' || !token) {
    throw new ApiError('The server returned an unexpected login response. Please try again.', response.status, 'BAD_LOGIN_RESPONSE');
  }
  setAuthToken(token);
  persistToken(token);
  return { expiresAtMs: getTokenExpiryMs(token) };
}

/** Explicit sign-out: clears the token from memory and sessionStorage. */
export function logout(): void {
  clearSession();
}

/* -------------------------------------------------------------------------------------------
 * Request helpers
 * ----------------------------------------------------------------------------------------- */
function assertApiPath(path: string): void {
  // Only same-origin API paths: the bearer token must never be sent to another host.
  if (!path.startsWith('/api/') || path.startsWith('//')) {
    throw new Error(`Refusing to call non-API path: ${path}`);
  }
}

async function toApiError(response: Response): Promise<ApiError> {
  const { code, message } = await readErrorBody(response);
  if (response.status === 429) {
    const retry = parseRetryAfter(response);
    return new ApiError(message || 'Too many requests. Please slow down and retry shortly.', 429, code, retry);
  }
  if (response.status === 403) {
    return new ApiError(message || 'This account is not allowed to access TRADEIQ.', 403, code);
  }
  if (response.status >= 500) {
    return new ApiError(message || 'The TRADEIQ server hit an error. Please try again shortly.', response.status, code);
  }
  return new ApiError(message || `HTTP ${response.status}`, response.status, code);
}

/**
 * Authenticated request to a PRIVATE endpoint.
 *  - No token => no network call: the caller is told to sign in (private APIs are never hit unauthenticated).
 *  - HTTP 401 => the session is cleared and subscribers (the app shell) are told to show the login screen.
 */
export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  assertApiPath(path);
  if (!authToken) {
    notifyUnauthorized();
    throw new ApiError('Please sign in to continue.', 401, 'UNAUTHORIZED');
  }
  const headers = new Headers(init?.headers || {});
  headers.set('Content-Type', 'application/json');
  headers.set('Authorization', `Bearer ${authToken}`);

  let response: Response;
  try {
    response = await fetch(path, { ...init, headers });
  } catch {
    throw new ApiError('Cannot reach the TRADEIQ server. Check your connection and try again.', 0, 'NETWORK_ERROR');
  }

  if (response.status === 401) {
    clearSession();
    notifyUnauthorized();
    throw new ApiError('Your session has expired. Please sign in again.', 401, 'UNAUTHORIZED');
  }
  if (!response.ok) throw await toApiError(response);
  return response.json() as Promise<T>;
}

export function apiGet<T>(path: string): Promise<T> {
  return apiFetch<T>(path);
}

export function apiPost<T>(path: string, body: unknown): Promise<T> {
  return apiFetch<T>(path, { method: 'POST', body: JSON.stringify(body) });
}

/** Public liveness probe (GET /api/v1/health is the only unauthenticated data endpoint). */
export async function fetchHealth(): Promise<any> {
  let response: Response;
  try {
    response = await fetch('/api/v1/health');
  } catch {
    throw new ApiError('Cannot reach the TRADEIQ server. Check your connection and try again.', 0, 'NETWORK_ERROR');
  }
  if (!response.ok) throw await toApiError(response);
  return response.json();
}

/**
 * Dashboard overview assembled from existing private endpoints
 * (/market/status, /demo/instruments, /demo/quotes/{symbol}). Requires a signed-in session.
 */
export async function fetchDemoOverview(): Promise<Envelope<{ quotes: any[] }>> {
  const [statusRes, instRes] = await Promise.allSettled([
    apiGet<any>('/api/v1/market/status'),
    apiGet<any>('/api/v1/demo/instruments'),
  ]);
  for (const r of [statusRes, instRes]) {
    if (r.status === 'rejected' && isUnauthorizedError(r.reason)) throw r.reason;
  }
  if (statusRes.status === 'rejected' && instRes.status === 'rejected') throw instRes.reason;

  const status = statusRes.status === 'fulfilled' ? statusRes.value : {};
  const symbols: string[] =
    instRes.status === 'fulfilled' ? (instRes.value?.instruments || []).map((i: any) => i.symbol).slice(0, 12) : [];

  const quoteResults = await Promise.allSettled(
    symbols.map((s) => apiGet<any>(`/api/v1/demo/quotes/${encodeURIComponent(s)}`)),
  );
  for (const r of quoteResults) {
    if (r.status === 'rejected' && isUnauthorizedError(r.reason)) throw r.reason;
  }
  const quotes = quoteResults.flatMap((r) => (r.status === 'fulfilled' && r.value?.quote ? [r.value.quote] : []));

  return {
    data: { quotes },
    source: quotes[0]?.provenance?.source || (status.demo_mode ? 'DEMO' : 'PROVIDER'),
    freshness: status.freshness || quotes[0]?.provenance?.freshness || 'SIMULATED',
    as_of_utc: new Date().toISOString(),
    market_session: status.market_state || 'CLOSED',
  };
}
