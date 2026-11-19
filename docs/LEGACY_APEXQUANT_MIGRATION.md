# ApexQuant v5.2 → TRADEIQ integration migration report

This document compares the supplied original `ApexQuant Dual-Market (Crypto + NSE) v5.2.0` source ZIP with the new TRADEIQ code.

## What the original source actually contained

The original ZIP included these real code paths (the archive did not include a `.env` file or secret values):

- `backend/angel_feed.py`: SmartAPI `generateSession()` using TOTP, instrument master download/token mapping, historical `getCandleData()`, and SmartWebSocketV2 subscription/tick handling.
- `backend/stock_live.py`: intraday NSE momentum radar consuming Angel One ticks and sending Telegram alerts.
- `backend/main.py`: Telegram `sendMessage` HTTP calls and REST operations on Supabase `paper_trades`.
- `backend/research_engine.py`: optional Supabase REST cache for `coin_research`.
- `backend/gemini_pool.py`: Gemini key-pool integration used by research and news workflows.
- `README.md`: Render start command and the environment variable names used by the previous deployment.

The old README documents Render deployment as `uvicorn backend.main:app --host 0.0.0.0 --port $PORT`. The supplied ZIP contains source/configuration, not proof that a currently running Render service is active.

## Compatibility carried into TRADEIQ

The new `backend/app/core/config.py` accepts the previous variable names:

| Previous Render variable | TRADEIQ configuration |
|---|---|
| `ANGEL_API_KEY` | `angel_api_key` |
| `ANGEL_CLIENT_CODE` | `angel_client_code` |
| `ANGEL_PIN` | `angel_password` |
| `ANGEL_TOTP_SECRET` | `angel_totp_secret` |
| `SUPABASE_URL` | `supabase_url` |
| `SUPABASE_KEY` | legacy key accepted for anon/service configuration |
| `GEMINI_API_KEYS` | multi-key Gemini pool |
| `TELEGRAM_BOT_TOKEN` | Telegram notifier |
| `TELEGRAM_CHAT_ID` | Telegram notifier |

The current `render.yaml` declares the legacy variable names as Render environment settings. Values are intentionally not embedded in this ZIP; configure them in Render's Environment page or local `.env`.

## Code migrated/implemented in this update

- `backend/app/providers/angel_one_provider.py` now includes SmartAPI login with TOTP, ScripMaster token parsing, historical candle retrieval, and SmartWebSocketV2 quote subscription. It is read-only and contains no broker order-placement call.
- `backend/app/main.py` creates the configured provider and attempts Angel One login, instrument refresh and WebSocket startup only when `DEMO_MODE=false`.
- Scanner/planner/backtest/alerts now receive the configured provider instead of constructing `DemoMarketDataProvider` directly.
- Market status no longer reports LIVE merely because `DEMO_MODE=false`; it requires recent WebSocket ticks.
- Telegram sends real `sendMessage` HTTP requests when credentials are configured. `POST /api/v1/notifications/telegram/test` sends an explicit test message when called with `{"confirm_send": true}`.
- `POST /api/v1/integrations/supabase/verify` checks the Supabase REST endpoint. A configured URL/key is not reported as a verified connection until the check succeeds.
- `LegacyPaperTradeStore` reuses the previous ApexQuant `paper_trades` table: it attempts to restore existing NSE paper rows at startup and insert/update rows when TRADEIQ paper positions are opened/closed. It tries the full legacy column set first, then the base column set used by the old code.
- Gemini's current provider-independent client accepts the old `GEMINI_API_KEYS` pool.
- Render build config now builds the Vite frontend and serves the built `frontend/dist` from FastAPI when present.
- Hard-coded example news stories were removed from the news API; it now fetches RSS/API news rather than fabricating stories.
- The insecure development fallback for `INTERNAL_JOB_TOKEN` was removed.

## Still not complete / not verified

These items must not be described as complete:

1. Only the legacy ApexQuant `paper_trades` table is bridged for NSE paper positions. The normalized TRADEIQ tables, user settings/watchlists, paper orders, and trading journal are not yet fully persisted. The `coin_research` table/cache from the old crypto project is not used by this NSE-only TRADEIQ app.
2. The legacy paper-trade bridge is single-owner compatibility only; it does not provide per-user isolation. API authentication/authorization is not enforced consistently on all private intelligence routes. Do not expose this deployment publicly with private user data until this is fixed.
3. The React feature screens remain partly placeholder modules. A production frontend build could not be run in this environment because `npm install` did not finish before timeout; run the documented build on Render or a machine with package registry access.
4. Live Angel One login, live historical candles, WebSocket ticks, Telegram delivery and Supabase connectivity have not been verified with the user's real credentials in this environment.
5. The Render YAML defaults to `DEMO_MODE=true` deliberately. Switch modes only after completing credentials, auth, persistence and deployment checks. Do not treat `DEMO_MODE=false` alone as proof of a live connection.
6. Render deployment itself has not been performed from this environment. The YAML is a deployment configuration, not proof of successful publication or an always-on instance.

## Verification performed for this update

- Backend test suite: 36 passed.
- Frontend dependency installation/build: not verified; `npm install` timed out in this environment.
- Real broker/database/Telegram network checks: not performed because no user credentials were supplied.

## Safe setup sequence

1. Keep `DEMO_MODE=true` while reviewing the updated project.
2. In Render, inspect the existing environment variable names and re-enter the user's own secret values; the ZIP intentionally contains no secrets.
3. Apply the TRADEIQ SQL migrations to a separate/test Supabase project first. Do not assume the old ApexQuant tables match the new schema.
4. Verify Supabase using `POST /api/v1/integrations/supabase/verify`.
5. Configure Angel One credentials, then set `DEMO_MODE=false` only in a private test deployment and inspect `/api/v1/providers/diagnostics` and `/api/v1/market/status`.
6. Verify Telegram using `POST /api/v1/notifications/telegram/test` with `{"confirm_send": true}`.
7. Do not use real-money execution; this project remains paper-trading only.
