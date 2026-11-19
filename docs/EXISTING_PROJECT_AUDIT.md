# Existing Project Audit — ApexQuant v5.2 to TRADEIQ

**Audit Date:** 18 November 2026 (IST)  
**Preserved Archive:** `backups/apexquant_v5_2_baseline/Market-Analysis-crypto--main.zip`  
**Baseline SHA-256:** `67a15db37ded8939604223f707685463b7c4d427f7cc161dcc4b75c1f7448ed7`  
**Total Entries Inspected:** 23 entries (11 Python backend modules, 2 HTML frontend files, SQL & config files)

---

## 1. End-to-End Data Flow Trace of Existing Project

### 1.1 Angel One Login & Token Handling (`backend/angel_feed.py`)
- **Flow:** `AngelFeed.login()` (lines 118–132) reads `ANGEL_API_KEY`, `ANGEL_CLIENT_CODE`, `ANGEL_PIN`, and `ANGEL_TOTP_SECRET` from environment variables, generates a 6-digit TOTP via `pyotp.TOTP(secret).now()`, and calls `SmartConnect.generateSession()`.
- **Instrument Master:** `load_equity_tokens()` (lines 73–104) streams the ~30 MB `OpenAPIScripMaster.json` from Angel Broking, extracts `NSE` `-EQ` tokens via regex without loading the entire JSON into memory, and caches the result locally in `angel_master_cache.json` by date (`YYYY-MM-DD`).
- **Gaps Identified:**
  - Only maps `symbol -> token`; drops `tick_size`, `lotsize`, ISIN, and instrument status.
  - Does not explicitly track the daily `00:00 IST` token expiry timestamp or expose a structured reconnect state to the UI when the JWT expires mid-session.

### 1.2 WebSocket Tick Path (`backend/angel_feed.py` & `backend/stock_live.py`)
- **Flow:** `AngelFeed.start()` (lines 151–194) spawns a daemon thread running `SmartWebSocketV2`, subscribes to equity tokens in Mode 2 (`quote: ltp, volume, vwap, ohlc`) in batches of 100, and normalizes prices from paise to float rupees (`float(msg["last_traded_price"]) / 100.0`).
- **Consumer:** `StockLiveRunner` (`stock_live.py`, lines 596–765) forwards ticks via `loop.call_soon_threadsafe(engine.on_tick, t)` into `LiveEngine.on_tick()` (lines 179–210), which records opening range (`09:15–09:30 IST`), evaluates 5-minute momentum, and streams snapshots over Server-Sent Events (`/api/stocks/live/stream`).
- **Gaps Identified:**
  - Converts integer paise immediately to binary `float` instead of `Decimal` or integer paise.
  - Uses local arrival time (`self.clock()`) rather than preserving both provider exchange timestamp and ingestion timestamp.
  - Persists intraday state to a local file (`intraday_journal.json`, line 38), which is wiped on ephemeral cloud container restarts.

### 1.3 Historical Candle Path (`backend/main.py`, `backend/angel_feed.py`, `backend/stock_premarket.py`)
- **Flow:**
  - `stock_live.py` (`_load_baselines`, lines 700–724) fetches 45 days of `ONE_DAY` candles from Angel One (`feed.historical`) with a `0.35s` sleep to respect the 3 req/sec limit.
  - However, `main.py` (`_sync_build_nse_scanner`, line 317 and `get_nse_chart`, line 1003) and `stock_premarket.py` (line 46) use **Yahoo Finance (`yfinance`)** for NSE swing scanning and charts!
- **Gaps Identified:**
  - **Source Split:** Live intraday uses Angel One, while Swing Scanner and Charts use `yfinance`. Prices, volumes, and corporate-action adjustments can diverge between the two sources.
  - **No Candle Validation/Quarantine:** No check for duplicate timestamps, out-of-order bars, `high < max(open, close)`, `low > min(open, close)`, or negative volume.
  - **No 5m-to-15m/30m/1h Aggregation Pipeline:** Intraday timeframes are fetched ad-hoc rather than derived deterministically from `09:15 IST`-anchored 5-minute bars.

### 1.4 Scanner & Trade-Plan Calculations (`backend/main.py`, `backend/analysis_engine.py`, `backend/stock_live.py`)
- **Flow:** Three separate modules implement separate scoring and target/stop formulas:
  1. `main.py` (`_sync_build_nse_scanner`, lines 346–435) computes EMA 20/50, RSI 14, ATR 14, ADX 14, OBV, and RVOL for 18 NSE stocks.
  2. `stock_live.py` (`evaluate`, lines 228–297) computes intraday ORB/PDH + VWAP + 5m volume surge.
  3. `analysis_engine.py` (`build_analysis`, lines 235–341) computes pivots, S/R levels, candlestick patterns, and a fixed-capital trade plan for crypto.
- **Gaps Identified:**
  - **Look-Ahead Risk in Pivots:** `pivots()` (`analysis_engine.py`, lines 43–51) checks `win = c[i - k : i + k + 1]`. While valid after `k` right-hand bars have closed, callers must enforce that a pivot at index `i` is only confirmed at bar `i + k`.
  - **Hard-Coded Capital & Risk Constants:**
    - `analysis_engine.py` (lines 15–18): `CAPITAL = 100000.0`, `RISK_PCT = 1.0`, `MAX_POS_PCT = 25.0`.
    - `stock_live.py` (lines 300–301): `INTRADAY_RISK_INR = 300.0`, `INTRADAY_MAX_POS_INR = 24500.0` (assumes 5x intraday leverage).
    - `main.py` (lines 749, 784): Fixed `budget_inr = 1000.0` (crypto) and `budget_inr = 5000.0` (NSE).
  - **No Cost-Aware All-In Risk Sizing:** Position sizing does not include itemized NSE brokerage, STT, exchange transaction charges, SEBI turnover fees, stamp duty, GST, and adverse slippage before computing maximum shares.

### 1.5 Paper-Trade Storage & API Endpoints (`backend/main.py`)
- **Flow:** `GET /api/trades`, `POST /api/trades`, `DELETE /api/trades/{trade_id}` (lines 1237–1268) read/write a flat `paper_trades` table in Supabase via REST, falling back silently to `trades_backup.json` (lines 475–543) if Supabase fails or lacks columns.
- **Gaps Identified:**
  - **Silent Local JSON Fallback:** Violates data integrity; if Supabase fails transiently, writes go to local disk and disappear on container restart.
  - **No User Ownership (`user_id`) or Auth Guard:** Endpoints are completely unauthenticated; anyone with the URL can create or delete paper trades.

### 1.6 Gemini & News Calls (`backend/gemini_pool.py`, `backend/research_engine.py`, `backend/stock_premarket.py`)
- **Flow:** `gemini_pool.py` rotates keys across `GEMINI_API_KEYS` and parses JSON loosely (`parse_json_loose`). `research_engine.py` and `stock_premarket.py` use Google Search grounding (`tools=[{"google_search": {}}]`).
- **Gaps Identified:**
  - No `fact_id` evidence packet or post-generation numeric grounding validator; if the LLM hallucinates a price or ratio in prose, it is not caught by a deterministic validator.

### 1.7 Security & CORS Risks (`backend/main.py`)
- `allow_origins=["*"]` in `CORSMiddleware` (`main.py`, line 897).
- Unauthenticated mutating endpoints (`POST /api/trades`, `DELETE /api/trades/{id}`, `POST /api/stocks/auto-cycle`, `POST /api/stocks/premarket/send`).
- No `private.provider_credentials` encryption at rest or `private.audit_log`.

---

## 2. Module Reuse / Refactor / Rebuild / Exclude Matrix

| Existing File | Decision | Target Location in `tradeiq/` | Action Plan |
| :--- | :---: | :--- | :--- |
| `backend/angel_feed.py` | **Refactor** | `backend/app/providers/angel_one.py` | Wrap behind `MarketDataProvider`; preserve streaming ScripMaster parser & TOTP flow; add provenance envelope (`source`, `as_of_ist`, `freshness`) and `Decimal`/paise normalization. |
| `backend/universe.py` | **Reuse as Seed** | `db/seed/` & `instrument_service.py` | Seed `market_instruments` table with canonical NSE symbols, tokens, and tick sizes. |
| `backend/analysis_engine.py` | **Extract & Refactor** | `backend/app/domain/indicators/` & `structure/` | Extract pure math (EMA, RSI, ATR, ADX, pivots, S/R, candle annotations); strip all hard-coded capital/risk constants; enforce confirmation-bar lag on pivots. |
| `backend/stock_live.py` | **Refactor Selectively** | `backend/app/services/scanner_service.py` | Replace custom inline scoring with shared `domain/strategies/` and `domain/risk/`; remove local JSON journal. |
| `backend/stock_premarket.py` | **Refactor Selectively** | `backend/app/services/market_context_service.py` | Keep market-context aggregation with explicit provenance and `UNAVAILABLE` flags when inputs fail. |
| `backend/gemini_pool.py` | **Refactor** | `backend/app/ai/gemini_client.py` | Keep key-rotation/cooldown mechanics; wrap behind `LLMProvider` interface with strict Pydantic validator and `fact_id` grounding check. |
| `backend/news_hub.py` | **Refactor (NSE Only)** | `backend/app/services/news_service.py` | Retain NSE/Indian financial RSS & API ingestion with deduplication, published timestamps, and untrusted-input sanitization; remove crypto feeds. |
| `backend/main.py` | **Rebuild** | `backend/app/main.py` + `api/v1/` | Replace monolithic file with modular FastAPI app factory, JWT auth dependencies, strict CORS allow-list, and structured JSON logging. |
| `frontend/index.html` | **Rebuild** | `frontend/src/` | Rebuild as modular React + TypeScript + Vite + Tailwind + TanStack Query + Lightweight Charts PWA. |
| `backend/crypto_engine.py`, `boom_engine.py`, `research_engine.py`, `data_sources.py`, `frontend/coin.html` | **Exclude** | N/A (Archived in backup) | Out of scope for TRADEIQ v1 (NSE cash equities only). |
