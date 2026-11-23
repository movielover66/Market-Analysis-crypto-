import datetime
from dataclasses import asdict
from decimal import Decimal
from typing import Any, Dict, List
from fastapi import APIRouter, Query, Request

from ....core.errors import TradeIQException
from ....domain.calendar.nse_calendar import get_market_state, to_ist
from ....domain.candles.validator import DomainCandle
from ....domain.indicators.core import compute_indicator_snapshot
from ....providers.angel_one_provider import AngelOneProvider
from ....services.instrument_service import InstrumentService

router = APIRouter(tags=["market-and-providers"])

_VALID_TIMEFRAMES = ("1m", "5m", "15m", "30m", "1h", "2h", "3h", "4h", "1d", "1M")


def _fmt_dec(val: Any) -> Any:
    if isinstance(val, Decimal):
        return str(val)
    if isinstance(val, dict):
        return {k: _fmt_dec(v) for k, v in val.items()}
    if isinstance(val, (list, tuple)):
        return [_fmt_dec(x) for x in val]
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
        provider_available = bool(
            health.get("websocket_connected")
            and health.get("last_tick_epoch")
            and (datetime.datetime.now().timestamp() - health["last_tick_epoch"] < 90)
        )
        freshness = "LIVE" if provider_available else "UNAVAILABLE"
    state = get_market_state(now_utc, provider_available=provider_available)
    return {
        "exchange": "NSE",
        "segment": "CASH_EQUITY",
        "market_state": state,
        "as_of_ist": to_ist(now_utc).isoformat(),
        "demo_mode": cfg.demo_mode,
        "freshness": freshness,
    }


@router.get("/market/overview")
async def market_overview(request: Request, limit: int = Query(12, ge=1, le=50)):
    """
    Unified Demo + Live Market Overview endpoint for the Dashboard & Market Overview pages.
    Works seamlessly in BOTH DEMO_MODE=true and DEMO_MODE=false (Angel One Live) without ever
    hitting /demo/* routes in live mode.
    """
    cfg = request.app.state.settings
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    provider = request.app.state.market_provider
    svc: InstrumentService = request.app.state.instrument_service

    if cfg.demo_mode:
        freshness, provider_available = "SIMULATED", True
    else:
        health = provider.status_summary() if hasattr(provider, "status_summary") else {}
        provider_available = bool(
            health.get("websocket_connected")
            and health.get("last_tick_epoch")
            and (datetime.datetime.now().timestamp() - health["last_tick_epoch"] < 90)
        )
        freshness = "LIVE" if provider_available else "UNAVAILABLE"

    market_state = get_market_state(now_utc, provider_available=provider_available)
    instruments = svc.search("", active_only=True)[:limit]
    quotes: List[Dict[str, Any]] = []
    advances = 0
    declines = 0
    sectors: Dict[str, Dict[str, Any]] = {}

    for inst in instruments:
        try:
            q = provider.get_quote(inst.symbol)
        except Exception:
            q = None
        if not q:
            continue
        q_dict = _fmt_dec(asdict(q))
        ltp = Decimal(str(q.ltp))
        prev = Decimal(str(q.prev_close))
        change_inr = (ltp - prev).quantize(Decimal("0.05"))
        change_pct = ((change_inr / prev) * Decimal("100")).quantize(Decimal("0.01")) if prev > 0 else Decimal("0.00")
        q_dict["change_inr"] = str(change_inr)
        q_dict["change_pct"] = str(change_pct)
        q_dict["sector"] = inst.sector
        q_dict["name"] = inst.name
        quotes.append(q_dict)

        if change_inr >= 0:
            advances += 1
        else:
            declines += 1
        sec = inst.sector or "General"
        if sec not in sectors:
            sectors[sec] = {"sector": sec, "count": 0, "advances": 0, "declines": 0}
        sectors[sec]["count"] += 1
        if change_inr >= 0:
            sectors[sec]["advances"] += 1
        else:
            sectors[sec]["declines"] += 1

    return {
        "exchange": "NSE",
        "segment": "CASH_EQUITY",
        "demo_mode": cfg.demo_mode,
        "source": "DEMO" if cfg.demo_mode else "ANGEL_ONE",
        "freshness": freshness,
        "market_session": market_state,
        "as_of_utc": now_utc.isoformat(),
        "as_of_ist": to_ist(now_utc).isoformat(),
        "breadth": {"advances": advances, "declines": declines, "total": len(quotes)},
        "sectors": list(sectors.values()),
        "quotes": quotes,
    }


@router.get("/market/candles/{symbol}")
async def get_symbol_candles(
    symbol: str,
    request: Request,
    timeframe: str = Query("15m"),
    limit: int = Query(45, ge=5, le=200),
):
    tf = timeframe if timeframe in _VALID_TIMEFRAMES else "15m"
    svc: InstrumentService = request.app.state.instrument_service
    inst = svc.resolve_symbol(symbol)
    if not inst:
        raise TradeIQException("NOT_FOUND", f"NSE instrument {symbol!r} not found", status_code=404)

    provider = request.app.state.market_provider
    raw = provider.get_candles(inst.symbol, timeframe=tf, limit=limit)
    if not raw:
        raise TradeIQException(
            "DATA_UNAVAILABLE",
            "No market candles are available from the configured provider; no demo fallback was used.",
            status_code=503,
        )
    candles = [_fmt_dec(asdict(c)) for c in raw]
    return {
        "symbol": inst.symbol,
        "instrument_id": inst.instrument_id,
        "timeframe": tf,
        "count": len(candles),
        "provenance": {
            "source": raw[-1].provenance.source,
            "freshness": raw[-1].provenance.freshness,
            "is_simulated": raw[-1].provenance.is_simulated,
            "as_of_ist": raw[-1].start_ts_ist,
        },
        "candles": candles,
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
async def get_symbol_indicators(
    symbol: str,
    request: Request,
    timeframe: str = Query("1d"),
):
    tf = timeframe if timeframe in _VALID_TIMEFRAMES else "1d"
    svc: InstrumentService = request.app.state.instrument_service
    inst = svc.resolve_symbol(symbol)
    if not inst:
        raise TradeIQException("NOT_FOUND", f"NSE instrument {symbol!r} not found", status_code=404)
    if inst.status != "ACTIVE":
        raise TradeIQException("INSTRUMENT_UNAVAILABLE", f"Instrument {inst.symbol} is {inst.status}", status_code=422)

    provider = request.app.state.market_provider
    raw = provider.get_candles(inst.symbol, timeframe=tf, limit=60)
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
        "timeframe": tf,
        "provenance": {
            "source": raw[-1].provenance.source,
            "freshness": raw[-1].provenance.freshness,
            "is_simulated": raw[-1].provenance.is_simulated,
            "as_of_ist": raw[-1].start_ts_ist,
        },
        "indicators": _fmt_dec(snap),
    }


@router.get("/market/indices")
async def get_live_indices(request: Request):
    """Returns LIVE NIFTY 50, BANKNIFTY, and SENSEX quotes from Angel One SmartAPI."""
    provider = request.app.state.market_provider
    indices = provider.get_index_quotes() if hasattr(provider, "get_index_quotes") else []
    return {
        "source": "DEMO" if request.app.state.settings.demo_mode else "ANGEL_ONE",
        "as_of_ist": to_ist(datetime.datetime.now(datetime.timezone.utc)).isoformat(),
        "indices": indices,
    }


@router.get("/market/depth/{symbol}")
async def get_symbol_depth(symbol: str, request: Request):
    """Returns LIVE 5-Level Buy/Sell Order-Book Depth & Imbalance Ratio."""
    from ....domain.strategies.three_engine import compute_order_book_imbalance
    sym = symbol.upper()
    provider = request.app.state.market_provider
    depth = provider.get_market_depth(sym) if hasattr(provider, "get_market_depth") else None
    ob = compute_order_book_imbalance(sym, depth=depth)
    return {
        "symbol": sym,
        "depth": depth,
        "imbalance": {
            "total_buy_qty": ob.total_buy_qty,
            "total_sell_qty": ob.total_sell_qty,
            "imbalance_ratio": str(ob.imbalance_ratio),
            "pressure": ob.pressure,
            "source": ob.source,
        },
    }
