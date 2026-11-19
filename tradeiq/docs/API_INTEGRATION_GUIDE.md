# TRADEIQ API & Integration Guide (`docs/API_INTEGRATION_GUIDE.md`)

## 1. Angel One SmartAPI (Read-Only Market Data)
- **Variables**: `ANGEL_API_KEY`, `ANGEL_CLIENT_CODE`, `ANGEL_PIN` (or `ANGEL_PASSWORD`), `ANGEL_TOTP_SECRET`
- **Static IP Rule**: Per [SmartAPI SEBI Guidelines](https://smartapi.angelone.in/smartapi/forum/topic/5254/important-updates-to-smartapi-in-compliance-with-sebi-guidelines), Static IP is mandatory **only** for Order Placement & GTT APIs. Read-only Market Data (`getCandleData`, Quotes, WebSocket V2) does **not** require a Static IP.
- **Rate Limit**: Historical candle requests are throttled to `2.5 req/s` (below the `3 req/s` limit per [SmartAPI Forum](https://smartapi.angelone.in/smartapi/forum/uid/25890)).
- **Daily Expiry**: JWT tokens expire at `00:00 IST` daily and automatically surface `RECONNECT_REQUIRED`.

## 2. Gemini Multi-Key Grounded AI (`GEMINI_API_KEY` / `GEMINI_API_KEYS`)
- Supports single key or comma-separated pool (`GEMINI_API_KEYS=key1,key2,key3`) with 90-second cooldown on HTTP 429.
- Every explanation is verified against `EvidencePacket` (`F01..Fnn`) and falls back to `DETERMINISTIC_TEMPLATE` if Gemini is unconfigured or fails validation.

## 3. Stock Market News Feeds
- **Zero-Key Default**: [NSE Official RSS](https://www.nseindia.com/static/rss-feed) and Google News India NSE RSS work automatically without any API key.
- **Optional Free Keys**: Set `FINNHUB_API_KEY` ([Finnhub](https://finnhub.io/docs/api/market-news)) or `MARKETAUX_API_KEY` ([Marketaux India](https://www.marketaux.com/news/country/in)) in `.env` for additional structured feeds.

## 4. Telegram Bot Notifications (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`)
- Automatically sends HTML alerts when closed-candle price/setup rules trigger.
