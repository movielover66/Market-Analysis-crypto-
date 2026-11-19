from dataclasses import asdict
from fastapi import APIRouter, Request
from ....core.errors import TradeIQException
from ....providers.demo_provider import DemoMarketDataProvider

router = APIRouter(prefix="/demo", tags=["demo-mode"])


def _serialize_decimal_dict(d: dict) -> dict:
    out = {}
    for k, v in d.items():
        if isinstance(v, dict):
            out[k] = _serialize_decimal_dict(v)
        elif hasattr(v, "quantize"):
            out[k] = str(v)
        else:
            out[k] = v
    return out


@router.get("/instruments")
async def list_demo_instruments(request: Request):
    cfg = request.app.state.settings
    provider = DemoMarketDataProvider(demo_mode_enabled=cfg.demo_mode)
    items = [_serialize_decimal_dict(asdict(i)) for i in provider.get_instruments()]
    return {
        "demo_mode": True,
        "banner": "DEMO MODE — SIMULATED DATA ONLY (NOT LIVE MARKET DATA)",
        "freshness": "SIMULATED",
        "instruments": items,
    }


@router.get("/quotes/{symbol}")
async def get_demo_quote(symbol: str, request: Request):
    cfg = request.app.state.settings
    provider = DemoMarketDataProvider(demo_mode_enabled=cfg.demo_mode)
    quote = provider.get_quote(symbol)
    if not quote:
        raise TradeIQException("NOT_FOUND", f"Demo instrument {symbol!r} not found", status_code=404)
    return {
        "demo_mode": True,
        "banner": "DEMO MODE — SIMULATED DATA ONLY (NOT LIVE MARKET DATA)",
        "quote": _serialize_decimal_dict(asdict(quote)),
    }
