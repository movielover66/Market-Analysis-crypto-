# Market Data Providers & Angel One SmartAPI Feasibility (`DATA_PROVIDERS.md`)

**Document Version:** 1.0  
**Prepared:** 18 November 2026 (IST)  
**Primary Broker Adapter:** Angel One SmartAPI (`AngelOneProvider`)  
**Fallback / Offline Adapter:** Deterministic Demo Provider (`DemoMarketDataProvider`)

---

## 1. Provider Architecture & Provenance Rules

Every quote and candle inside TRADEIQ must come through the `MarketDataProvider` interface and carry a mandatory **Provenance Envelope**:
- `source`: `"ANGEL_ONE"` | `"DEMO"`
- `exchange_ts`: UTC/IST timestamp from exchange/provider
- `ingested_ts`: UTC timestamp when received by TRADEIQ backend
- `freshness`: `"LIVE"` | `"DELAYED"` | `"HISTORICAL"` | `"SIMULATED"` | `"UNAVAILABLE"` (plus computed `"STALE"` when age exceeds threshold during market hours)
- `quality_status`: `"VALID"` | `"QUARANTINED"` | `"PROVISIONAL"`

**Hard Rule:** If `DEMO_MODE=true` or `DemoMarketDataProvider` is active, all data is tagged `"SIMULATED"` and a permanent banner is displayed. If `AngelOneProvider` is active and fails or times out, TRADEIQ must surface `"UNAVAILABLE"` or `"STALE"` (`NO TRADE`)—it must **never** silently substitute simulated demo data in live mode.

---

## 2. Angel One SmartAPI — Official Specifications & SEBI Rules (2025–2026)

Based on official Angel One SmartAPI documentation and SEBI Retail Algorithmic Trading circulars (`SEBI/HO/MIRSD/MIRSD-PoD/P/CIR/2025/0000013`):

| Parameter / Requirement | Official Specification | Impact on TRADEIQ v1 |
| :--- | :--- | :--- |
| **Static IP Requirement** | Mandatory **only** for Order Placement & GTT APIs. For APIs other than Orders & GTT (Login, Quotes, Historical Candles `getCandleData`, WebSocket V2), a Static IP is **not** mandatory. | Since TRADEIQ v1 is strictly **Paper Trading Only** (zero broker order placement), market-data ingestion works without a paid static IP. |
| **Authentication Flow** | `SmartConnect.generateSession(client_code, pin, totp)` using `ANGEL_API_KEY`, `ANGEL_CLIENT_CODE`, `ANGEL_PIN`, and `ANGEL_TOTP_SECRET` (base32 secret). | Supported via backend-only environment variables or encrypted `private.provider_credentials`. |
| **Session / Token Expiry** | JWT and Feed tokens expire daily at **00:00 hours (midnight IST)** or upon new session generation. | Adapter must detect expired tokens, mark provider status as `RECONNECT_REQUIRED`, and re-authenticate cleanly before the `09:00 IST` pre-market window. |
| **Historical Candle API (`getCandleData`)** | Rate limit: **3 requests per second** (exceeding returns HTTP 403 `"Access denied because of exceeding access rate"`). Supports `ONE_MINUTE`, `FIVE_MINUTE`, `FIFTEEN_MINUTE`, `THIRTY_MINUTE`, `ONE_HOUR`, `ONE_DAY`. | Ingestion service must enforce a token-bucket rate limiter (`<= 2.5 req/sec`) with exponential backoff on HTTP 403/429. |
| **Scrip Master File** | `OpenAPIScripMaster.json` (~30 MB JSON containing all exchange instruments, tokens, and tick sizes). | Stream-parse once daily, filter `exch_seg == "NSE"` and `symbol.endswith("-EQ")`, and upsert into `market_instruments`. |
| **SmartWebSocketV2** | Supports up to 1,000 instrument tokens per connection across modes (`Mode 1 = LTP`, `Mode 2 = Quote`, `Mode 3 = SnapQuote`). Prices are transmitted in **integer paise**. | Convert integer paise directly to `Decimal` rupees (`Decimal(paise) / Decimal(100)`) without binary float intermediates. |

---

## 3. Verified vs. Unverified Items Checklist

| Item | Status | Notes / Verification Plan |
| :--- | :---: | :--- |
| `MarketDataProvider` interface & `DemoMarketDataProvider` | **Verified (Local)** | Can be tested 100% offline in CI using deterministic golden fixtures. |
| ScripMaster streaming parser (`NSE -EQ` symbols, tokens, tick sizes) | **Verified (Public URL)** | Public JSON endpoint; parser tested in existing `angel_feed.py` and will be extended to capture `tick_size` and `lotsize`. |
| Static IP exemption for read-only Market Data / Historical / WebSocket | **Verified (Policy)** | Confirmed via Angel One SmartAPI official forum announcements (Feb–Sep 2026). |
| User's specific Angel One account eligibility & TOTP secret validity | **Unverified (Requires User Credentials)** | Must be tested by user in local/staging `.env` during Phase 2 Milestone 2.3. Until verified with live credentials, live feed remains marked `UNVERIFIED / DEMO_MODE`. |
| Corporate-action adjustment (splits/bonuses) in Angel One historical candles | **Unverified (Requires Live Check)** | Must be inspected on known recent split/bonus stocks during Phase 2.4; adjustment status (`UNADJUSTED` vs `ADJUSTED`) will be stored on every candle. |
