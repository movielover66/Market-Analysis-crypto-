import datetime
from dataclasses import asdict
from decimal import Decimal
from typing import Optional
from fastapi import APIRouter, Query, Request

from ....core.errors import TradeIQException
from ....domain.calendar.nse_calendar import get_market_state, to_ist
from ....domain.candles.validator import DomainCandle
from ....domain.indicators.core import compute_indicator_snapshot
from ....providers.angel_one_provider import AngelOneProvider
from ....providers.demo_provider import DemoMarketDataProvider
from ....services.instrument_service import InstrumentService

router = APIRouter(tags=["market-and-providers"])


def _fmt_dec(val):
    if isinstance(val, Decimal):
        return str(val)
    if isinstance(val, dict):
        return {k: _fmt_dec(v) for k, v in val.items()}
    return val


@router.get("/market/status")
async def market_status(request: Request):
    cfg = request.app.state.settings
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    provider = request.app.state.market_provider
    if cfg.demo_mode:
        freshness, provider_available = "SIMULATED", True
    else:
        health = provider.status_summary() if hasattr(provider, "status_summary") else {}
        provider_available = bool(health.get("websocket_connected") and health.get("last_tick_epoch") and (datetime.datetime.now().timestamp() - health["last_tick_epoch"] < 90))
        freshness = "LIVE" if provider_available else "UNAVAILABLE"
    state = get_market_state(now_utc, provider_available=provider_available)
    return {
        "exchange": "NSE", "segment": "CASH_EQUITY", "market_state": state,
        "as_of_ist": to_ist(now_utc).isoformat(), "demo_mode": cfg.demo_mode,
        "freshness": freshness,
    }


@router.get("/providers/diagnostics")
async def provider_diagnostics(request: Request):
    cfg = request.app.state.settings
    provider = request.app.state.market_provider
    angel = provider if isinstance(provider, AngelOneProvider) else None
    return {
        "active_provider": "DEMO" if cfg.demo_mode else "ANGEL_ONE",
        "demo_mode": cfg.demo_mode,
        "provider_startup_error": request.app.state.provider_startup_error,
        "angel_one": angel.status_summary() if angel else {"configured": False, "connection_state": "DEMO_MODE"},
        "read_only_market_data": True,
    }


@router.get("/market/instruments")
async def list_instruments(
    request: Request,
    q: str = Query(""),
    active_only: bool = Query(False),
):
    cfg = request.app.state.settings
    svc: InstrumentService = request.app.state.instrument_service
    items = [_fmt_dec(asdict(i)) for i in svc.search(q, active_only=active_only)]
    return {
        "exchange": "NSE",
        "demo_mode": cfg.demo_mode,
        "source": "DEMO" if cfg.demo_mode else "ANGEL_ONE",
        "freshness": "SIMULATED" if cfg.demo_mode else ("HISTORICAL" if items else "UNAVAILABLE"),
        "count": len(items),
        "instruments": items,
    }


@router.get("/market/indicators/{symbol}")
async def get_symbol_indicators(symbol: str, request: Request):
    cfg = request.app.state.settings
    svc: InstrumentService = request.app.state.instrument_service
    inst = svc.resolve_symbol(symbol)
    if not inst:
        raise TradeIQException("NOT_FOUND", f"NSE instrument {symbol!r} not found", status_code=404)
    if inst.status != "ACTIVE":
        raise TradeIQException("INSTRUMENT_UNAVAILABLE", f"Instrument {inst.symbol} is {inst.status}", status_code=422)

    provider = request.app.state.market_provider
    raw = provider.get_candles(inst.symbol, timeframe="1d", limit=60)
    if not raw:
        raise TradeIQException(
            "DATA_UNAVAILABLE",
            "No market candles are available from the configured provider; no demo fallback was used.",
            status_code=503,
        )

    dom_candles = [
        DomainCandle(
            symbol=inst.symbol,
            timeframe=c.timeframe,
            ts_ist=datetime.datetime.fromisoformat(c.start_ts_ist),
            open=c.open,
            high=c.high,
            low=c.low,
            close=c.close,
            volume=c.volume,
            is_closed=c.is_closed,
            source=c.provenance.source,
            freshness=c.provenance.freshness,
            quality_status=c.provenance.quality_status,
            adjustment_status=c.adjustment_status,
        )
        for c in raw
    ]
    snap = compute_indicator_snapshot(dom_candles)
    return {
        "symbol": inst.symbol,
        "instrument_id": inst.instrument_id,
        "timeframe": "1d",
        "provenance": {
            "source": raw[-1].provenance.source,
            "freshness": raw[-1].provenance.freshness,
            "is_simulated": raw[-1].provenance.is_simulated,
            "as_of_ist": raw[-1].start_ts_ist,
        },
        "indicators": _fmt_dec(snap),
    }
