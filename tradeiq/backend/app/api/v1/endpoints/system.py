import datetime
from fastapi import APIRouter, Request

# Public: needed for the Render health check. Reveals nothing beyond liveness and the demo/live banner.
public_router = APIRouter(tags=["system"])
# Private: mounted behind owner auth in api/v1/router.py.
router = APIRouter(tags=["system"])


@public_router.get("/health")
async def api_v1_health(request: Request):
    settings = request.app.state.settings
    return {
        "status": "ok",
        "service": "tradeiq-backend",
        "version": "0.1.0",
        "demo_mode": settings.demo_mode,
        "freshness_banner": "SIMULATED" if settings.demo_mode else "LIVE_OR_PROVIDER",
        "ts_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }


@router.get("/system/status")
async def system_status(request: Request):
    settings = request.app.state.settings
    return {
        "project": "TRADEIQ",
        "release_stage": "development_beta",
        "exchange": "NSE",
        "segment": "CASH_EQUITY",
        "trading_modes": ["INTRADAY", "SWING"],
        "execution_mode": "PAPER_ONLY",
        "live_order_execution": False,
        "config": settings.safe_summary(),
    }
