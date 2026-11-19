# TRADEIQ — AI-Assisted NSE Cash Equity Trading Intelligence Platform

**Release Status:** Development / Beta (Paper Trading Only — No Real-Money Order Execution)  
**Target Market:** Indian Stock Market — NSE Cash Equities (`-EQ`)  
**Trading Modes:** Intraday (5m / 15m / 1h) & Swing (1h / 4h / 1d)

## Core Engineering Principle
Market data, strategy decisions, position sizing, risk limits, and backtest fills are **100% deterministic, testable, and reproducible**. Gemini AI explains verified evidence packets (`fact_id`) but can never invent prices, generate signals independently, alter risk calculations, or place orders.

## Repository Structure
- `backend/` — Modular Python FastAPI application (`app/core`, `app/api/v1`, `app/domain`, `app/providers`, `app/services`, `app/repositories`, `app/ai`, `app/jobs`, `tests`).
- `frontend/` — Mobile-first React + TypeScript + Vite + Tailwind CSS + TanStack Query + Lightweight Charts PWA.
- `db/` — Supabase PostgreSQL SQL migrations (`db/migrations/`) and deterministic demo seed data (`db/seed/`).
- `docs/` — Architecture, Existing Project Audit, Data Providers, Strategies, Risk Model, Backtesting, Security, and Runbook documentation.

## Quick Start (Local Development)

### 1. Backend Setup
```bash
cd tradeiq/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp ../.env.example ../.env
uvicorn app.main:app --reload --port 8000
```
Verify health endpoint: `http://127.0.0.1:8000/health` and `http://127.0.0.1:8000/api/v1/system/status`

### 2. Run Backend Tests
```bash
cd tradeiq/backend
python3 -m unittest discover -s tests -v
# Or with pytest:
pytest -v
```

## Legacy ApexQuant integration migration

This codebase was compared with the supplied ApexQuant Dual-Market v5.2.0 source. Previous Render variable names are supported, and Angel One SmartAPI login/TOTP, instrument master, historical candles and WebSocket quote adapter plus live Telegram sending have been migrated into the modular TRADEIQ app. See [`docs/LEGACY_APEXQUANT_MIGRATION.md`](docs/LEGACY_APEXQUANT_MIGRATION.md) for exact scope and limitations.

**Important:** Supabase persistence and API authorization are not yet fully complete, frontend feature modules are still partly placeholders, and no real account credentials were used to verify external connections. Keep `DEMO_MODE=true` until those remaining items are addressed. This project does not place live broker orders.
