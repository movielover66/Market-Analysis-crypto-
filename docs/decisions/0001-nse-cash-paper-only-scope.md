# ADR 0001: NSE Cash Equities (-EQ) & Paper Trading Only Scope
- **Status**: Accepted
- **Context**: Legacy `ApexQuant v5.2` mixed Binance crypto spot and NSE stocks with simulated floating-point math.
- **Decision**: Lock TRADEIQ v1 strictly to NSE Cash Equities (`-EQ`), Intraday + Swing modes, and Paper Trading only (zero live broker order placement).
