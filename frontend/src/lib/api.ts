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
    return null;
  }
}

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
    /* ignore */
  }
}

export function clearSession(): void {
  setAuthToken(null);
  try {
    sessionStore()?.removeItem(SESSION_TOKEN_KEY);
  } catch {
    /* ignore */
  }
}

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
      /* ignore */
    }
  });
}

async function readErrorBody(response: Response): Promise<{ code?: string; message?: string }> {
  const body = await response.json().catch(() => ({}));
  const detailMsg = typeof body?.detail === 'string' ? body.detail : body?.detail?.message;
  return { code: body?.error?.code, message: body?.error?.message || detailMsg };
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

export function logout(): void {
  clearSession();
}

function assertApiPath(path: string): void {
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

export function apiPut<T>(path: string, body: unknown): Promise<T> {
  return apiFetch<T>(path, { method: 'PUT', body: JSON.stringify(body) });
}

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
 * Unified Market Overview (works in BOTH Demo Mode and Live Angel One Mode via /api/v1/market/overview).
 */
export async function fetchDemoOverview(): Promise<Envelope<{ quotes: any[]; breadth?: any; sectors?: any[] }>> {
  const ov = await apiGet<any>('/api/v1/market/overview?limit=12');
  return {
    data: {
      quotes: ov?.quotes || [],
      breadth: ov?.breadth || { advances: 0, declines: 0, total: 0 },
      sectors: ov?.sectors || [],
    },
    source: ov?.source || 'DEMO',
    freshness: ov?.freshness || 'SIMULATED',
    as_of_utc: ov?.as_of_utc || new Date().toISOString(),
    market_session: ov?.market_session || 'CLOSED',
  };
}
