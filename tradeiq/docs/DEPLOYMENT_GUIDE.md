# Deployment Guide (Render)

The included `render.yaml` builds the React/Vite frontend and the FastAPI backend in one Render web service. It is a blueprint, not proof that a deployment has succeeded.

## Before deployment

- Keep `DEMO_MODE=true` for the first deployment.
- Set `SUPABASE_URL`, `SUPABASE_KEY`, `ANGEL_API_KEY`, `ANGEL_CLIENT_CODE`, `ANGEL_PIN`, `ANGEL_TOTP_SECRET`, `GEMINI_API_KEYS`, `TELEGRAM_BOT_TOKEN`, and `TELEGRAM_CHAT_ID` in Render Environment using your own values. These legacy ApexQuant names are supported.
- Set a strong `INTERNAL_JOB_TOKEN`; Render generates one from the blueprint.
- Never put secrets in Git or in this ZIP.
- A Render free web service may sleep; a persistent Angel One WebSocket feed needs an always-on service plan and stable process uptime. Check current Render plan behaviour before relying on intraday feed continuity.

## After deployment

- Check `/health` and `/api/v1/integrations/status`.
- Verify Supabase with `POST /api/v1/integrations/supabase/verify`.
- Send a deliberate Telegram test with `POST /api/v1/notifications/telegram/test` and JSON body `{"confirm_send": true}`.
- For Angel One, use a private test deployment, set `DEMO_MODE=false`, then check `/api/v1/providers/diagnostics` and `/api/v1/market/status`. `LIVE` is shown only after recent WebSocket ticks.
- Do not switch to live provider mode until the database/authentication gaps in `LEGACY_APEXQUANT_MIGRATION.md` are resolved.

## Current limitations

Supabase data persistence and consistent API authorization are still incomplete. Frontend build and real external credentials were not verified in this environment. Read `docs/LEGACY_APEXQUANT_MIGRATION.md` before deployment.
