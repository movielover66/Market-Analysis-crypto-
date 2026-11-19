# TRADEIQ Deterministic Decimal Risk & NSE Cost Model (`docs/RISK_MODEL.md`)

All monetary, position-sizing, exposure, and fee calculations are implemented in `backend/app/domain/risk/risk_engine.py` using Python's ` decimal.Decimal` (`ROUND_HALF_UP` for currency, `ROUND_FLOOR` for share quantity).

## 1. Default Guardrail Parameters
- **Starting Paper Capital**: `₹75,000.00` (configurable `₹50,000`–`₹1,00,000+`)
- **Max Risk Per Trade**: `0.50%` of capital (`₹375.00` on `₹75,000`)
- **Max Daily Loss Limit**: `1.00%` of capital (`₹750.00` on `₹75,000`)
- **Max Weekly Loss Limit**: `2.50%` of capital (`₹1,875.00` on `₹75,000`)
- **Max Concurrent Open Positions**: `2`
- **Max Single-Position Exposure**: `25.0%` of capital (`₹18,750.00` on `₹75,000`)
- **Minimum Net Reward-to-Risk (`Net R:R`)**: `1:2.0` after deducting all round-trip statutory and slippage charges.

## 2. Position Sizing Formula
1. `risk_per_share = |entry_price - stop_loss|`
2. `max_risk_budget_inr = capital_inr * (risk_per_trade_pct / 100)`
3. `qty_by_risk = floor(max_risk_budget_inr / risk_per_share)`
4. `qty_by_exposure = floor((capital_inr * max_single_position_exposure_pct / 100) / entry_price)`
5. `approved_quantity = min(qty_by_risk, qty_by_exposure)`

## 3. Itemized NSE Cash Equity Cost Model (`CostProfile`)
- **Brokerage**: Configurable % capped at `₹20.00` per leg.
- **STT (Securities Transaction Tax)**:
  - **Delivery (`SWING`)**: `0.10%` on both buy and sell value.
  - **Intraday (`INTRADAY`)**: `0.025%` on sell leg only.
- **Exchange Transaction Charge**: `0.00297%` on total turnover.
- **SEBI Turnover Fee**: `0.0001%` on total turnover.
- **Stamp Duty**: `0.015%` (`SWING` buy leg) or `0.003%` (`INTRADAY` buy leg).
- **GST**: `18.0%` on `(Brokerage + Exchange Transaction Charge + SEBI Fee)`.
- **Slippage**: Configurable basis points (`default 5 bps` per leg).

## 4. Machine-Readable Rejection Codes
- `STALE_DATA_REJECTED`
- `INACTIVE_INSTRUMENT`
- `DAILY_LOSS_LIMIT_REACHED`
- `WEEKLY_LOSS_LIMIT_REACHED`
- `MAX_OPEN_POSITIONS_REACHED`
- `SWING_SHORT_NOT_SUPPORTED`
- `INVALID_PRICE_LEVELS`
- `STRATEGY_OR_MTF_UNCONFIRMED`
- `EXPOSURE_OR_RISK_BUDGET_TOO_SMALL`
- `NET_RR_BELOW_MINIMUM`
- `NET_RISK_EXCEEDS_BUDGET`
