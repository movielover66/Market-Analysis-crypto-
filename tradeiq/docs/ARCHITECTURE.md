# TRADEIQ — Locked Technical Architecture & Scope (`ARCHITECTURE.md`)

**Version:** 1.0 (Phase 0 Freeze)  
**Date:** 18 November 2026  
**Status:** Locked per `TRADEIQ_Full_Development_Plan.md`

---

## 1. Frozen Initial Scope (ADR-0001)

| Dimension | Locked Decision |
| :--- | :--- |
| **Market & Exchange** | Indian Stock Market — **NSE Cash Equities (`-EQ`) only** |
| **Trading Styles** | **Intraday** (5m / 15m / 1h) and **Swing** (1h / 4h / 1d) |
| **Execution Mode** | **Paper Trading Only** — zero real-money order placement or GTT execution |
| **Capital Range** | User-configurable (`Decimal`), initial default range **₹50,000–₹1,00,000** |
| **Default Risk Guardrails** | `0.5%` risk/trade, `1.0%` daily loss lock, max `2` open positions, max `25%` single-position exposure, min `1:2` net R:R |
| **Market Data Adapter** | `AngelOneProvider` (SmartAPI) + `DemoMarketDataProvider` behind `MarketDataProvider` interface |
| **AI Provider** | `Gemini` behind provider-independent `LLMProvider` interface with `fact_id` Evidence Packet & Validator |
| **Out of Scope (v1)** | Crypto, Futures & Options (F&O), commodities, live order execution, multi-user public SaaS |

---

## 2. Six Invariant Architectural Rules

1. **Pure Domain Isolation:** `backend/app/domain/` is 100% pure Python. It must never import from `api/`, `repositories/`, `providers/`, `services/`, or `ai/`. Enforced by automated AST import-lint tests in CI.
2. **Single Strategy & Risk Codebase:** The live scanner, trade planner, paper simulator, and bar-by-bar backtester must invoke the exact same `domain/strategies/` and `domain/risk/` classes.
3. **Mandatory Provenance Envelope:** Every market quote and candle carries `(value, source, exchange_ts_ist, ingested_ts_utc, freshness, quality_status)`.
4. **Freshness Gate:** Freshness classes are `LIVE`, `DELAYED`, `HISTORICAL`, `SIMULATED`, `UNAVAILABLE`, plus computed `STALE`. Stale or unavailable data automatically forces `NO TRADE`.
5. **Closed-Candle Confirmation:** Only closed candles (`is_closed=True`) can confirm a strategy setup or trigger an alert. In-progress candles are marked `PROVISIONAL`.
6. **Exact Monetary Arithmetic:** All prices, fees, slippage, risk budgets, and P&L calculations use Python `Decimal` (or integer paise) rounded to the instrument's valid `tick_size`—never binary floating-point (`float`).
