# TRADEIQ Operations & Beginner Runbook (`docs/RUNBOOK.md`)

## 1. Running Backend & Test Suite Locally
```bash
cd tradeiq/backend
python3 -m unittest discover -s tests -v
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## 2. Running Frontend Locally
```bash
cd tradeiq/frontend
npm install
npm run dev
```

## 3. Switching Between Offline Demo Mode and Angel One SmartAPI
- **Default (`DEMO_MODE=true`)**: Runs 100% offline with deterministic NSE Cash Equity (`-EQ`) fixtures (`RELIANCE`, `TCS`, `HDFCBANK`, `INFY`, `ICICIBANK`, `SBIN`). Every payload carries `source: "DEMO"` and `freshness: "SIMULATED"`.
- **Angel One SmartAPI (`DEMO_MODE=false`)**:
  - Set `ANGEL_ONE_API_KEY`, `ANGEL_ONE_CLIENT_CODE`, `ANGEL_ONE_PIN`, and `ANGEL_ONE_TOTP_SECRET` in `.env`.
  - Per [Angel One SmartAPI SEBI Guidelines](https://smartapi.angelone.in/smartapi/forum/topic/5254/important-updates-to-smartapi-in-compliance-with-sebi-guidelines), Static IP is **not** required for read-only Market Data (`getCandleData`, Quotes, WebSocket V2).
  - Historical `getCandleData` is throttled to `2.5 req/s` inside `AngelOneProvider` to stay below the 3 req/s hardware limit ([SmartAPI Forum](https://smartapi.angelone.in/smartapi/forum/uid/25890)).
  - Daily JWT expiry at `00:00 IST` automatically transitions provider status to `RECONNECT_REQUIRED`.
