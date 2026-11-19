# ADR 0003: Pure Python Domain Boundary & Decimal Risk Engine
- **Status**: Accepted
- **Context**: Binary floating-point causes rounding drift in position sizing and statutory NSE fee calculations.
- **Decision**: Enforce AST-verified zero external imports in `backend/app/domain/` and use `Decimal` (`ROUND_HALF_UP` for INR, `ROUND_FLOOR` for quantity) across risk, paper ledger, and backtesting.
