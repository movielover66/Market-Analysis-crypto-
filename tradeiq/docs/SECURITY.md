# TRADEIQ Security, Privacy & Guardrail Architecture (`docs/SECURITY.md`)

## 1. Zero Hardcoded Secrets & At-Rest Credential Encryption
- All secrets (`JWT_SECRET_KEY`, `ENCRYPTION_MASTER_KEY`, `ANGEL_ONE_*`, `GEMINI_API_KEY`) are loaded strictly from environment variables via `backend/app/core/config.py`.
- User broker credentials stored in `private.broker_credentials_encrypted` are encrypted using `CredentialCipher` (`backend/app/core/crypto.py`) with unique per-record random salt/nonce and authenticated `HMAC-SHA256` verification.

## 2. Recursive Secret Scrubbing in Logs
- `SecretScrubbingFilter` (`backend/app/core/logging.py`) recursively redacts sensitive keys (`password`, `secret`, `token`, `totp`, `jwt`, `authorization`, `api_key`) across all structured JSON logs before output.

## 3. Database Row-Level Security (RLS)
- `db/migrations/0002_rls_policies.sql` enables PostgreSQL Row-Level Security on all 21 user-facing tables (`auth.uid() = user_id`) and revokes `anon`/`authenticated` access from the `private` schema.

## 4. AI Grounding & Prompt-Injection Sanitization
- External news headlines and summaries pass through `sanitize_untrusted_text()` (`backend/app/ai/grounding.py`) to neutralize prompt-injection patterns before reaching any AI prompt.
- Gemini AI operates strictly on immutable `EvidencePacket` facts (`F01..Fnn`), is validated by `validate_ai_explanation()` to reject any invented numbers, and automatically falls back to `TEMPLATE_FALLBACK` after 1 failed validation retry.

## 5. Single-Owner Authentication & Endpoint Protection
- **One identity.** Every authenticated request resolves to `settings.owner_id`. A validly signed token for any other
  identity gets `403`. Identity is taken only from the verified token — never from request bodies or query strings
  (a legacy `user_id` field sent by an old client is ignored).
- **Two ways in, same owner:** built-in login `POST /api/v1/auth/login` (scrypt hash in `OWNER_PASSWORD_HASH`, session
  JWT signed with `AUTH_JWT_SECRET`, default TTL 60 min) and/or Supabase access tokens whose `sub` equals `OWNER_USER_ID`
  (`aud=authenticated`, `role=authenticated` enforced; `service_role`/`anon` keys are rejected).
- **Fail closed.** With no owner auth configured, every private endpoint returns `401` and login returns `503`.
  Staging/production refuse to boot without an owner auth method.
- **Secure by default routing.** Auth is attached at router level (`api/v1/router.py`); new endpoints on those routers are
  protected automatically. Public surface is exactly: `GET /health`, `GET /api/v1/health`, `POST /api/v1/auth/login`,
  the static SPA shell, and `POST /api/v1/jobs/trigger` (machine credential `X-Internal-Job-Token`, not an owner session).
  `tests/test_security_auth.py` fails if any other route becomes reachable without credentials.
- **Brute-force protection.** 5 failed logins per client / 15 min and 20 globally, then `429` + `Retry-After`.
- **Other hardening:** API docs/OpenAPI off unless `ENABLE_API_DOCS=true`; CORS wildcard forbidden and credentials disabled
  (bearer tokens only); `Cache-Control: no-store` on `/api/*`; `nosniff`/`DENY`/`no-referrer` headers; Telegram message text
  HTML-escaped; scanner capped at 30 symbols per call.
- **Paper only.** No broker order-placement code or route exists; `live_order_execution` is `false`.

### Setup
```
cd backend && python -m app.core.password_hash      # prints OWNER_PASSWORD_HASH
python -c "import secrets; print(secrets.token_urlsafe(48))"   # AUTH_JWT_SECRET
```
Set both on Render, plus `APP_ENV=production` once verified. Frontend flow: `POST /api/v1/auth/login {"password": ...}`
then `setAuthToken(access_token)` (in `frontend/src/lib/api.ts`).

### Frontend login (owner-only UI)
- The React app shows an owner login screen first (`frontend/src/components/LoginScreen.tsx`); the workspace is not even
  mounted, and **no private API is called**, until a session exists. The backend-served fallback page
  (`backend/app/static/index.html`) behaves the same way and keeps its controls hidden until sign-in.
- The access token is held in memory and mirrored to **`sessionStorage`** (cleared when the tab closes, so a page refresh
  keeps you signed in). **`localStorage` is never used.** No password or token is hard-coded anywhere in the UI.
- Sign out, an HTTP `401` from any private call, or the token's own `exp` passing clears the token and returns to the login
  screen. A wrong password (`401` from the login call itself) only shows an error; `429` shows the lockout wait.
- All private calls go through `apiFetch()` (Bearer header, same-origin `/api/` paths only). There is no signup and no
  multi-user UI.
- Checks: `frontend/tests/auth_session.test.ts` (`npm run test:auth`), `backend/tests/test_frontend_owner_login_contract.py`,
  and `frontend/e2e/static_fallback_login.check.cjs` (browser check of the fallback page).

### Known limitations
See the project report: tokens are stateless (no server-side logout/revocation; rely on the short TTL), the login throttle and all
trading state are in-memory (single worker only), and the legacy Supabase `paper_trades` table has no per-user column.
