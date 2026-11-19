# TRADEIQ Bar-by-Bar Backtesting & Out-of-Sample Methodology (`docs/BACKTESTING.md`)

Implemented in `backend/app/domain/backtest/engine.py`.

## 1. Execution & Fill Rules (Zero Look-Ahead Bias)
1. **Closed-Bar Signal Evaluation**: At bar index `i`, only bars `0..i` (`state == "CLOSED"`) are visible to the indicator, market-structure, and strategy engines.
2. **Next-Bar Open Fill**: When a signal triggers at the close of bar `i`, the entry order is filled at the **open of bar `i + 1`** plus adverse entry slippage (`slippage_bps`).
3. **Conservative Same-Bar Conflict Resolution**: If bar `j`'s `[low, high]` range touches both the Stop Loss and Target price on the same candle, the engine **always resolves Stop Loss first** (`EXIT_CONFLICT_STOP_FIRST`).
4. **Gap-Through-Stop Handling**: If bar `j` opens beyond the Stop Loss (`open < stop_loss` for a `LONG` trade), the exit fills at the adverse `bar.open` (`GAP_THROUGH_STOP`) rather than the nominal stop price.

## 2. In-Sample vs Out-of-Sample (Walk-Forward) Split
- Every backtest splits the historical candle series into an **In-Sample (`IS`)** segment (default `70%`) and an **Out-of-Sample (`OOS`)** segment (default `30%`), reporting separate `PerformanceReport` metrics (`win_rate_pct`, `profit_factor`, `expectancy_inr`, `max_drawdown_pct`) for each segment to detect overfitting.

## 3. Repeatability Hash (`result_hash`)
- Every backtest computes a deterministic `SHA-256` digest over `(symbol, timeframe, strategy_code, first_bar_utc, last_bar_utc, initial_capital_inr, trades)`. Identical inputs always produce the exact same `result_hash`.
