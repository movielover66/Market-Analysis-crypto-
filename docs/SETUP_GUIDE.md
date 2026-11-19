# TRADEIQ Setup Guide (`docs/SETUP_GUIDE.md`)

## 1. Prerequisites
- Python 3.11+ (`backend/`)
- Node.js 18+ (`frontend/`, optional when using Unified FastAPI Render mode)
- Supabase PostgreSQL project (optional in Demo Mode; required for persistent cloud storage)

## 2. Step-by-Step Setup
1. Copy `.env.example` to `.env`:
   ```bash
   cp .env.example .env
   ```
2. In Supabase SQL Editor, run:
   - `db/migrations/0001_initial_schema.sql`
   - `db/migrations/0002_rls_policies.sql`
   - `db/seed/seed_demo.sql` (optional demo instruments)
3. Start the backend server:
   ```bash
   cd backend
   uvicorn app.main:app --reload --port 8000
   ```
