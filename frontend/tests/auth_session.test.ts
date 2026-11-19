/**
 * Owner-auth session tests for src/lib/api.ts (no browser or test framework needed).
 * Run:  npx tsx tests/auth_session.test.ts
 * Verifies: login request shape, sessionStorage (never localStorage), error messages (401/429/5xx/network),
 * no private call without a token, 401 => session cleared + listeners notified, expired token not restored.
 */
import assert from 'node:assert/strict';

class MemStorage {
  private m = new Map<string, string>();
  getItem(k: string) { return this.m.has(k) ? this.m.get(k)! : null; }
  setItem(k: string, v: string) { this.m.set(k, String(v)); }
  removeItem(k: string) { this.m.delete(k); }
  get size() { return this.m.size; }
}
const session = new MemStorage();
const local = new MemStorage();
let localTouched = false;
const trappedLocal = new Proxy(local, { get(t, p, r) { localTouched = true; return Reflect.get(t, p, r); } });
(globalThis as any).window = { sessionStorage: session, localStorage: trappedLocal };

type Call = { url: string; init?: RequestInit };
let calls: Call[] = [];
let handler: (url: string, init?: RequestInit) => Response | Promise<Response> = () => new Response('{}', { status: 200 });
(globalThis as any).fetch = async (url: string, init?: RequestInit) => {
  calls.push({ url, init });
  return handler(url, init);
};
const json = (status: number, body: unknown, headers: Record<string, string> = {}) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json', ...headers } });

// Fake JWT (header.payload.sig). Test-only; the signature is meaningless here.
const b64 = (o: unknown) => Buffer.from(JSON.stringify(o)).toString('base64url');
const fakeJwt = (expSec: number) => `${b64({ alg: 'HS256', typ: 'JWT' })}.${b64({ sub: 'owner', exp: expSec })}.sig`;
const future = () => fakeJwt(Math.floor(Date.now() / 1000) + 3600);

async function main() {
  const api = await import('../src/lib/api');
  let passed = 0;
  const test = async (name: string, fn: () => Promise<void> | void) => {
    api.clearSession(); session.removeItem(api.SESSION_TOKEN_KEY); calls = [];
    await fn();
    passed += 1;
    console.log(`  ok  ${name}`);
  };

  await test('login posts exactly {"password":...} to /api/v1/auth/login', async () => {
    handler = () => json(200, { access_token: future(), token_type: 'bearer', expires_in: 3600 });
    await api.loginOwner('s3cret pass');
    assert.equal(calls.length, 1);
    assert.equal(calls[0].url, '/api/v1/auth/login');
    assert.equal(calls[0].init?.method, 'POST');
    assert.deepEqual(JSON.parse(String(calls[0].init?.body)), { password: 's3cret pass' });
    assert.equal(new Headers(calls[0].init?.headers).get('Authorization'), null);
  });

  await test('login success: token set via setAuthToken and mirrored to sessionStorage only', async () => {
    const tok = future();
    handler = () => json(200, { access_token: tok });
    await api.loginOwner('x');
    assert.equal(api.getAuthToken(), tok);
    assert.equal(session.getItem(api.SESSION_TOKEN_KEY), tok);
    assert.equal(local.size, 0);
    assert.equal(localTouched, false, 'localStorage must never be accessed');
  });

  await test('restoreSession re-arms token after "refresh"; expired token is dropped', async () => {
    const tok = future();
    session.setItem(api.SESSION_TOKEN_KEY, tok);
    assert.equal(api.restoreSession(), true);
    assert.equal(api.getAuthToken(), tok);
    api.setAuthToken(null);
    session.setItem(api.SESSION_TOKEN_KEY, fakeJwt(Math.floor(Date.now() / 1000) - 10));
    assert.equal(api.restoreSession(), false);
    assert.equal(api.getAuthToken(), null);
    assert.equal(session.getItem(api.SESSION_TOKEN_KEY), null);
  });

  await test('wrong password => 401 message, no session, global unauthorized NOT fired', async () => {
    let fired = 0; const off = api.onUnauthorized(() => { fired += 1; });
    handler = () => json(401, { error: { code: 'UNAUTHORIZED', message: 'Invalid credentials' } });
    await assert.rejects(() => api.loginOwner('bad'), (e: any) => e instanceof api.ApiError && e.status === 401 && /incorrect password/i.test(e.message));
    assert.equal(api.getAuthToken(), null); assert.equal(fired, 0); off();
  });

  await test('429 => rate-limit message with Retry-After', async () => {
    handler = () => json(429, { error: { code: 'RATE_LIMITED', message: 'Too many failed login attempts.' } }, { 'Retry-After': '600' });
    await assert.rejects(() => api.loginOwner('x'), (e: any) => e.status === 429 && e.retryAfterSec === 600 && /too many failed attempts/i.test(e.message) && /10 minutes/.test(e.message));
  });

  await test('503 / 500 / network failure => clear server messages', async () => {
    handler = () => json(503, { error: { code: 'AUTH_NOT_CONFIGURED', message: 'x' } });
    await assert.rejects(() => api.loginOwner('x'), (e: any) => e.status === 503 && /not configured/i.test(e.message));
    handler = () => json(500, { error: { code: 'INTERNAL_SERVER_ERROR', message: 'boom' } });
    await assert.rejects(() => api.loginOwner('x'), (e: any) => e.status === 500 && /server hit an error/i.test(e.message));
    handler = () => { throw new TypeError('Failed to fetch'); };
    await assert.rejects(() => api.loginOwner('x'), (e: any) => e.status === 0 && /cannot reach/i.test(e.message));
  });

  await test('malformed 200 login response (no access_token) is rejected', async () => {
    handler = () => json(200, { nope: true });
    await assert.rejects(() => api.loginOwner('x'), (e: any) => e.code === 'BAD_LOGIN_RESPONSE');
    assert.equal(api.getAuthToken(), null);
  });

  await test('NO private API call is made without a token', async () => {
    let fired = 0; const off = api.onUnauthorized(() => { fired += 1; });
    await assert.rejects(() => api.apiGet('/api/v1/scanner'), (e: any) => e.status === 401);
    await assert.rejects(() => api.fetchDemoOverview(), (e: any) => e.status === 401);
    assert.equal(calls.length, 0, 'fetch must not be called');
    assert.ok(fired >= 1); off();
  });

  await test('authenticated calls send the Bearer token', async () => {
    const tok = future(); api.setAuthToken(tok);
    handler = () => json(200, { ok: true });
    await api.apiGet('/api/v1/paper/summary');
    assert.equal(new Headers(calls[0].init?.headers).get('Authorization'), `Bearer ${tok}`);
  });

  await test('HTTP 401 on a private call clears token + storage and notifies', async () => {
    const tok = future(); api.setAuthToken(tok); session.setItem(api.SESSION_TOKEN_KEY, tok);
    let fired = 0; const off = api.onUnauthorized(() => { fired += 1; });
    handler = () => json(401, { error: { code: 'UNAUTHORIZED', message: 'Token has expired' } });
    await assert.rejects(() => api.apiGet('/api/v1/journal'), (e: any) => api.isUnauthorizedError(e));
    assert.equal(api.getAuthToken(), null); assert.equal(session.getItem(api.SESSION_TOKEN_KEY), null); assert.equal(fired, 1); off();
  });

  await test('logout clears memory + sessionStorage', async () => {
    const tok = future(); api.setAuthToken(tok); session.setItem(api.SESSION_TOKEN_KEY, tok);
    api.logout();
    assert.equal(api.getAuthToken(), null); assert.equal(session.getItem(api.SESSION_TOKEN_KEY), null);
  });

  await test('token is never sent to non-API / cross-origin paths', async () => {
    api.setAuthToken(future());
    await assert.rejects(() => api.apiFetch('https://evil.example/steal'), /non-API path/);
    await assert.rejects(() => api.apiFetch('//evil.example/api/x'), /non-API path/);
    assert.equal(calls.length, 0);
  });

  await test('403 (non-owner token) and 5xx surface readable messages', async () => {
    api.setAuthToken(future());
    handler = () => json(403, { error: { code: 'FORBIDDEN', message: 'single-user' } });
    await assert.rejects(() => api.apiGet('/api/v1/journal'), (e: any) => e.status === 403);
    assert.ok(api.getAuthToken(), '403 must not clear the session');
    handler = () => json(500, { error: { message: 'An unexpected internal error occurred.' } });
    await assert.rejects(() => api.apiGet('/api/v1/journal'), (e: any) => e.status === 500);
  });

  console.log(`\n${passed} auth/session tests passed`);
}

main().catch((e) => { console.error('FAILED:', e); process.exit(1); });
