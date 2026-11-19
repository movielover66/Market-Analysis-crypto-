# TRADEIQ Troubleshooting Guide (`docs/TROUBLESHOOTING_GUIDE.md`)

1. **`DEMO_MODE_DISABLED` or Startup Validation Error in Production**:
   - If `DEMO_MODE=false` in staging/production, ensure `ANGEL_API_KEY` and `ANGEL_CLIENT_CODE` are set in `.env`. TRADEIQ never silently fakes live market data when `DEMO_MODE=false`.
2. **Angel One `RECONNECT_REQUIRED` after Midnight IST**:
   - SmartAPI JWT tokens expire at `00:00 IST` daily. Call reconnect or restart the session before `09:15 IST`.
3. **Gemini Quota / HTTP 429**:
   - Add multiple keys from separate projects in `GEMINI_API_KEYS=key1,key2`. Even if all keys hit quota, TRADEIQ automatically serves verified `DETERMINISTIC_TEMPLATE` explanations without crashing.
