# TRADEIQ Test Report

Date: 2026-11-18

## Backend

Command:

```bash
cd backend && pytest -q
```

Result: **37 passed**.

Python compile check:

```bash
python -m compileall -q backend
```

Result: passed.

Warnings observed:
- FastAPI `on_event` lifecycle handlers are deprecated and should be migrated to lifespan handlers.
- One Pydantic v2 deprecation warning: `.dict()` should become `.model_dump()`.

## Frontend

`npm install --no-audit --no-fund` was attempted, but dependency installation timed out in this environment. Therefore `npm run typecheck` and `npm run build` were **not verified** here. The Render blueprint runs the frontend install and build as part of deployment.

## External integrations

No real user credentials were provided, so these were not live-verified:
- Angel One SmartAPI login / TOTP
- Angel One historical candle API
- Angel One WebSocket subscription and real ticks
- Supabase REST access against the user's project
- Telegram delivery to the user's chat
- Gemini API request using the user's key pool
- Render deployment / persistent uptime

Mocked unit tests cover adapter request/response handling only; they do not prove external account connectivity.

## Known limitations

- Only legacy ApexQuant `paper_trades` persistence has a compatibility adapter.
- Normalized TRADEIQ persistence for settings, watchlists, paper orders and journal remains incomplete.
- Authentication is not enforced consistently across all private intelligence endpoints.
- Some frontend feature modules remain placeholders.
- Do not use as a public multi-user deployment or for real-money execution.
