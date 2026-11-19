# ADR 0002: Angel One SmartAPI Read-Only Market Data & Static IP Rules
- **Status**: Accepted
- **Context**: SEBI retail algo guidelines introduced Static IP registration for order execution APIs.
- **Decision**: Per [SmartAPI Forum Topic 5254](https://smartapi.angelone.in/smartapi/forum/topic/5254/important-updates-to-smartapi-in-compliance-with-sebi-guidelines), Static IP is mandatory only for Order Placement & GTT APIs. TRADEIQ uses read-only Market Data (`getCandleData` throttled at 2.5 req/s below the 3 req/s limit per [SmartAPI Forum UID 25890](https://smartapi.angelone.in/smartapi/forum/uid/25890)) and handles daily `00:00 IST` JWT expiry via `RECONNECT_REQUIRED`.
