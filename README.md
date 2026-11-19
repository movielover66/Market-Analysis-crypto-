# TRADEIQ v2.0 — 3-Engine Live Auto Paper-Trading Intelligence Platform

**Mode:** 100% Live Market Data (`DEMO_MODE=false` default) — **Paper Trading Only** (Zero Real-Money Broker Orders)  
**Markets:** NSE Cash Equities (`-EQ`) + Live **NIFTY 50**, **BANKNIFTY**, and **SENSEX** Tracking

## What's New in v2.0
- **3-Engine Multi-Regime Strategy Suite (`backend/app/domain/strategies/three_engine.py`)**:
  - **Engine 0 (Market Regime Gate):** Classifies session as `TRENDING`, `RANGE_BOUND`, or `CHOPPY` using live Nifty/BankNifty/Sensex + Breadth + ATR; automatically blocks entries on choppy days.
  - **Engine 1 (Morning Intraday 09:30–11:30 IST):** 15m Opening Range Breakout (ORB) + VWAP + Relative Volume Surge + Angel One 5-Level Order-Book Imbalance (`SnapQuote`).
  - **Engine 2A (Swing Breakout):** Mark Minervini Stage-2 Trend Alignment + Volatility Contraction Pattern (VCP).
  - **Engine 2B (Swing Mean Reversion):** Larry Connors RSI(2) Pullback on strong uptrend stocks.
- **Autonomous Paper-Trader (`backend/app/jobs/auto_trader.py`)**:
  - Editable **Account Balance** & **Per-Trade Amount (₹)** directly from UI.
  - **Charge-Buffered Breakeven SL** at `1.5R`, **Trailing SL** at `2.0R`, **15:15 IST Intraday Square-Off**, **3-Consecutive-Loss & Daily-Loss Kill-Switch**, and **Daily Telegram Summary**.
- **Penny-Accurate ("পাই টু পাই") P&L Ledger (`backend/app/domain/paper/simulator.py`)**:
  - Live Mark-to-Market deducts **both entry AND estimated exit NSE charges**.
  - Strict `WIN` / `LOSS` / `BREAKEVEN` classification strictly by `net_pnl_inr` sign.
  - Verified equity invariant: `starting_balance + realised_net + unrealised_net == total_equity`.
- **Real Multi-Source Indian Market News + Gemini News Sentiment Shield (`backend/app/services/alerts_and_news.py`)**:
  - Aggregates Economic Times Markets, ET Stocks, Livemint Markets, Moneycontrol, and Google News India RSS with Gemini 2-line summaries & `HIGH_RISK`/`BEARISH` BUY shield.
- **Brand-New Menubar Dashboard UI (`frontend/src/app/App.tsx` + `backend/app/static/index.html`)**:
  - Dedicated visible top menubar (zero hidden "Others/More" drawer), live Indices strip, 5-level depth imbalance view, and full single-owner JWT login security on all private APIs.

## Quick Verification
```bash
cd tradeiq/backend
python3 -m unittest discover -s tests -v
```
