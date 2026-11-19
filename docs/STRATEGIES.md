# TRADEIQ Deterministic Strategy Specifications (`docs/STRATEGIES.md`)

All strategies in `TRADEIQ` reside in `backend/app/domain/strategies/templates.py` and operate strictly on closed, validated `CandleBar` series (`state == "CLOSED"`), `IndicatorSnapshot`, and `MarketStructureReport` without look-ahead bias.

## 1. Strategy A — Breakout + Retest (`BREAKOUT_RETEST`)
- **Direction**: `LONG`
- **Market Regime**: `UPTREND` or `SIDEWAYS`
- **Trigger Conditions**:
  - Latest closed candle closes above a clustered resistance zone (`resistance_zone.center_price`) or recent swing high (`last_swing_high`).
  - Relative Volume (`rvol >= 1.10`) confirms participation.
  - Wilder `RSI(14)` is between `50` and `78` (momentum without extreme exhaustion).
- **Stop Loss**: Placed below the retest zone / candle low minus `0.5 * ATR(14)` (or `1.5 * ATR(14)` below entry).
- **Target Price**: Entry plus `2.8 * (Entry - Stop)` (subject to the Risk Engine's net R:R `>= 2.0` gate after NSE costs).

## 2. Strategy B — Support Bounce (`SUPPORT_BOUNCE`)
- **Direction**: `LONG`
- **Market Regime**: `UPTREND` or `SIDEWAYS`
- **Trigger Conditions**:
  - Price tests a multi-touch support zone (`touch_count >= 2`) and closes bullishly (`close > open`) above support.
  - `RSI(14)` is between `35` and `65`.
- **Stop Loss**: Support zone lower bound minus `0.5 * ATR(14)`.
- **Target Price**: Entry plus `2.6 * (Entry - Stop)`.

## 3. Strategy C — Trend Following Pullback (`TREND_FOLLOWING`)
- **Direction**: `LONG`
- **Market Regime**: `UPTREND` (`HH_HL` structure)
- **Trigger Conditions**:
  - `EMA(9) > EMA(20) > EMA(50)` alignment with `Close > EMA(20)`.
  - Positive MACD histogram (`macd_hist > 0`) and `RSI(14)` in `[45, 72]`.
- **Stop Loss**: `min(EMA(20) - 0.8 * ATR(14), Entry - 1.4 * ATR(14))`.
- **Target Price**: Entry plus `2.8 * (Entry - Stop)`.

## 4. Strategy D — Breakdown + Retest (`BREAKDOWN_RETEST` — Intraday Short Only)
- **Direction**: `SHORT` (permitted in `INTRADAY` mode only; automatically rejected in `SWING` delivery mode by `RiskEngine`).
- **Market Regime**: `DOWNTREND` (`LH_LL`)
- **Trigger Conditions**:
  - `EMA(9) < EMA(20)` and `RSI(14) < 48` with `MACD Histogram < 0`.
- **Stop Loss**: `Entry + 1.5 * ATR(14)`.
- **Target Price**: `Entry - 2.8 * (Stop - Entry)`.

## 5. Multi-Timeframe Alignment (`backend/app/domain/strategies/mtf_aligner.py`)
- **Intraday Mode**: Requires `5m` (Execution), `15m` (Setup), and `1h` (Bias) alignment.
- **Swing Mode**: Requires `1h` (Entry Timing), `4h` (Setup), and `1d` (Primary Trend) alignment.
