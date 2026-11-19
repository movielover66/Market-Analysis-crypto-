/**
 * Browser check for backend/app/static/index.html (the backend-served fallback page).
 * The API is mocked at the network layer; the Tailwind CDN is deliberately blocked to prove the page
 * never shows private controls when the CDN is unavailable.
 * Run:  node e2e/static_fallback_login.check.cjs   (needs `playwright` + a Chromium install)
 */
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const { chromium } = require('playwright');

const HTML = fs.readFileSync(path.resolve(__dirname, '../../backend/app/static/index.html'), 'utf8');
const PAGE = 'http://tradeiq.test/'; // served from memory so same-origin /api/... paths resolve like in production
const b64 = (o) => Buffer.from(JSON.stringify(o)).toString('base64url');
const jwt = (expSec) => `${b64({ alg: 'HS256', typ: 'JWT' })}.${b64({ sub: 'owner', exp: expSec })}.sig`;
const TOKEN = jwt(Math.floor(Date.now() / 1000) + 3600);

(async () => {
  const browser = await chromium.launch();
  let passed = 0;
  const ok = (m) => { passed += 1; console.log('  ok  ' + m); };

  async function newPage(handlers) {
    const ctx = await browser.newContext({ viewport: { width: 360, height: 800 } });
    const page = await ctx.newPage();
    const reqs = [];
    await page.route('https://cdn.tailwindcss.com/**', (r) => r.abort());
    await page.route('http://tradeiq.test/**', async (route) => {
      const req = route.request();
      const u = new URL(req.url());
      if (!u.pathname.startsWith('/api/')) return route.fulfill({ status: 200, contentType: 'text/html', body: HTML });
      reqs.push({ method: req.method(), path: u.pathname + u.search, auth: req.headers()['authorization'] || null, body: req.postData() });
      await handlers(route, u, req);
    });
    return { page, reqs, ctx };
  }
  const fulfill = (route, status, body, headers = {}) =>
    route.fulfill({ status, contentType: 'application/json', headers, body: JSON.stringify(body) });

  // 1) Unauthenticated load: only login is visible, nothing private is requested.
  {
    const { page, reqs, ctx } = await newPage(async (r) => fulfill(r, 500, {}));
    await page.goto(PAGE);
    assert.equal(await page.locator('#login-view').isVisible(), true);
    assert.equal(await page.locator('#app-view').isVisible(), false);
    assert.equal(await page.locator('[data-action]:visible').count(), 0, 'no workspace controls visible before login');
    assert.equal(reqs.length, 0, 'no API calls before authentication');
    ok('unauthenticated: login only, zero API calls, controls hidden (even with CDN blocked)');
    await ctx.close();
  }

  // 2-7) Full flow
  let mode = 'wrong';
  const { page, reqs, ctx } = await newPage(async (route, u, req) => {
    if (u.pathname === '/api/v1/auth/login') {
      const body = JSON.parse(req.postData() || '{}');
      if (mode === 'wrong') return fulfill(route, 401, { error: { code: 'UNAUTHORIZED', message: 'Invalid credentials' } });
      if (mode === 'limited') return fulfill(route, 429, { error: { code: 'RATE_LIMITED', message: 'Too many' } }, { 'Retry-After': '5' });
      if (mode === 'down') return fulfill(route, 503, { error: { code: 'AUTH_NOT_CONFIGURED', message: 'x' } });
      assert.deepEqual(Object.keys(body), ['password']);
      return fulfill(route, 200, { access_token: TOKEN, token_type: 'bearer', expires_in: 3600 });
    }
    if (!req.headers()['authorization']) return fulfill(route, 401, { error: { message: 'Authentication required' } });
    if (u.pathname === '/api/v1/scanner' && mode === 'expire') return fulfill(route, 401, { error: { message: 'Token has expired' } });
    return fulfill(route, 200, { source: 'DEMO', freshness: 'SIMULATED', hello: 'world' });
  });

  await page.goto(PAGE);

  mode = 'wrong';
  await page.fill('#owner-password', 'nope');
  await page.click('#login-submit');
  await page.waitForSelector('#login-error:visible');
  assert.match(await page.textContent('#login-error'), /incorrect password/i);
  assert.equal(await page.locator('#app-view').isVisible(), false);
  assert.equal(await page.inputValue('#owner-password'), '', 'password field cleared after failure');
  ok('wrong password => "Incorrect password" and still on login');

  mode = 'down';
  await page.fill('#owner-password', 'x'); await page.click('#login-submit');
  await page.waitForFunction(() => /not configured/i.test(document.getElementById('login-error').textContent));
  ok('503 => "not configured" message');

  mode = 'limited';
  await page.fill('#owner-password', 'x'); await page.click('#login-submit');
  await page.waitForFunction(() => /too many failed attempts/i.test(document.getElementById('login-error').textContent));
  assert.equal(await page.locator('#login-submit').isDisabled(), true);
  assert.match(await page.textContent('#login-submit'), /Try again in/);
  ok('429 => rate-limit message with disabled button + countdown');
  await page.waitForFunction(() => !document.getElementById('login-submit').disabled, null, { timeout: 9000 });

  mode = 'good';
  await page.fill('#owner-password', 'correct horse');
  await page.click('#login-submit');
  await page.waitForSelector('#app-view:visible');
  assert.equal(await page.locator('#login-view').isVisible(), false);
  const loginReq = reqs.filter((r) => r.path === '/api/v1/auth/login').pop();
  assert.deepEqual(JSON.parse(loginReq.body), { password: 'correct horse' });
  assert.equal(loginReq.auth, null);
  await page.waitForFunction(() => document.getElementById('output-panel').textContent.includes('hello'));
  const afterLogin = reqs.slice(reqs.indexOf(loginReq) + 1);
  assert.ok(afterLogin.length >= 1 && afterLogin.every((r) => r.auth === `Bearer ${TOKEN}`), 'every private call carries Bearer');
  assert.equal(await page.evaluate(() => window.sessionStorage.getItem('tradeiq.owner.session')), TOKEN);
  assert.equal(await page.evaluate(() => window.localStorage.length), 0, 'localStorage untouched');
  ok('login success => workspace visible, Bearer on all private calls, token in sessionStorage only');

  const before = reqs.length;
  await page.reload();
  await page.waitForSelector('#app-view:visible');
  await page.waitForFunction(() => document.getElementById('output-panel').textContent.includes('hello'));
  assert.ok(reqs.slice(before).every((r) => r.auth === `Bearer ${TOKEN}`));
  ok('page refresh keeps the session (no re-login)');

  mode = 'expire';
  await page.click('[data-action="scanner"]');
  await page.waitForSelector('#login-view:visible');
  assert.equal(await page.locator('#app-view').isVisible(), false);
  assert.match(await page.textContent('#login-notice'), /session has expired/i);
  assert.equal(await page.evaluate(() => window.sessionStorage.getItem('tradeiq.owner.session')), null);
  ok('HTTP 401 on a private call => token cleared, login screen with expiry notice');

  mode = 'good';
  await page.fill('#owner-password', 'correct horse'); await page.click('#login-submit');
  await page.waitForSelector('#app-view:visible');
  await page.click('#logout-btn');
  await page.waitForSelector('#login-view:visible');
  assert.equal(await page.evaluate(() => window.sessionStorage.getItem('tradeiq.owner.session')), null);
  const n = reqs.length;
  await page.reload();
  await page.waitForSelector('#login-view:visible');
  assert.equal(reqs.length, n, 'no API calls after sign-out + reload');
  ok('sign out => storage cleared, reload shows login, zero private calls');

  const overflow = await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);
  assert.equal(overflow, true, 'no horizontal overflow at 360px');
  ok('no horizontal overflow at 360px');

  await ctx.close();
  await browser.close();
  console.log(`\n${passed} static-page browser checks passed`);
})().catch((e) => { console.error('FAILED:', e); process.exit(1); });
