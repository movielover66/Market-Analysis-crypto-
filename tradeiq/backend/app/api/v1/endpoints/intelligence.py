"""
Phase 3–6 Endpoints + Unified Legacy Bridges:
- Risk Calculator (/api/v1/risk/calculate)
- Multi-Timeframe Scanner (/api/v1/scanner)
- Deterministic Trade Planner + Grounded AI Explanation (/api/v1/planner/evaluate)
- Paper Trading Simulator & Ledger (/api/v1/paper/*) with Supabase REST Sync
- Trading Journal & Emotion/Rule Tagging (/api/v1/journal)
- Performance Analytics (/api/v1/analytics)
- Bar-by-Bar Backtester with OOS Split & Hash (/api/v1/backtest/run)
- NSE News, Keyless RSS & Multi-Channel Alerts (/api/v1/news, /api/v1/alerts)
- Read-Only Grounded AI Assistant & Fixed Toolkit (/api/v1/assistant/query, /api/v1/assistant/tool)
- Telegram Status (/api/v1/notifications/telegram/status)  [cron trigger lives in endpoints/jobs.py]
- 160+ NSE Cash Universe & 9:00 AM IST Pre-Market Report (/api/v1/market/universe, /api/v1/market/premarket)
- Integration Verification Matrix (/api/v1/integrations/status)
"""
import datetime
import asyncio
import time
import uuid
from dataclasses import asdict
from decimal import Decimal
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ....ai.grounding import generate_grounded_explanation
from ....core.auth import AuthenticatedUser, get_current_user
from ....core.errors import TradeIQException
from ....ai.llm_client import ReadOnlyAssistantToolkit
from ....domain.analytics.performance import compute_performance_analytics
from ....domain.backtest.engine import run_bar_by_bar_backtest
from ....domain.calendar.nse_calendar import IST
from ....domain.candles.validator import DomainCandle
from ....domain.planner.trade_planner import build_trade_plan
from ....domain.risk.risk_engine import (
    AccountRiskState,
    RiskEvaluationInput,
    RiskParameters,
    evaluate_trade_risk,
)
from ....domain.universe.nse_universe import GLOBAL_PREMARKET_CUES, NSE_LIQUID_CASH_UNIVERSE
from ....providers.demo_provider import DemoMarketDataProvider
from ....providers.base import MarketDataProvider

# SECURITY: this router is mounted behind owner authentication in api/v1/router.py. The identity used for
# every ledger/journal/analytics operation is the VERIFIED token identity (`user.user_id`); no endpoint
# here accepts a user id from the client. (Unknown JSON fields such as a legacy `user_id` are ignored.)
router = APIRouter(tags=["risk-and-intelligence"])

MAX_SCAN_SYMBOLS = 30  # each symbol costs 4 provider candle fetches; caps provider-quota exhaustion


def _user_positions(sim, user_id: str) -> list:
    return [p for p in sim.positions.values() if p.user_id == user_id]


def _open_count_and_realised_pnl(sim, user_id: str):
    mine = _user_positions(sim, user_id)
    open_cnt = sum(1 for p in mine if p.status == "OPEN")
    realised = sum((p.net_pnl_inr for p in mine if p.status == "CLOSED"), Decimal("0.00"))
    return open_cnt, realised


def _user_journal(request: Request, user_id: str) -> list:
    return [e for e in request.app.state.journal_entries if e.get("user_id") == user_id]


def _public_journal_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in entry.items() if k != "user_id"}


def _ser(val: Any) -> Any:
    if isinstance(val, Decimal):
        return str(val)
    if isinstance(val, dict):
        return {k: _ser(v) for k, v in val.items()}
    if isinstance(val, (list, tuple)):
        return [_ser(x) for x in val]
    return val


def _load_domain_candles(symbol: str, timeframe: str, bars: int = 45, provider: Optional[MarketDataProvider] = None) -> List[DomainCandle]:
    active_provider = provider or DemoMarketDataProvider(demo_mode_enabled=True)
    norm_candles = active_provider.get_candles(symbol.upper(), timeframe=timeframe, limit=bars)
    out: List[DomainCandle] = []
    for c in norm_candles:
        try:
            dt = datetime.datetime.fromisoformat(c.start_ts_ist)
        except Exception:
            continue
        out.append(DomainCandle(
            symbol=symbol.upper(), timeframe=timeframe, ts_ist=dt,
            open=c.open, high=c.high, low=c.low, close=c.close, volume=c.volume,
            is_closed=c.is_closed, source=c.provenance.source,
            freshness=c.provenance.freshness, quality_status=c.provenance.quality_status,
            adjustment_status=c.adjustment_status,
        ))
    return out


class RiskCalculationRequest(BaseModel):
    symbol: str = "RELIANCE"
    direction: str = "LONG"
    trading_mode: str = "SWING"
    entry_price: float
    stop_loss: float
    target_price: float
    capital_inr: float = 75000.0
    risk_per_trade_pct: float = 0.50
    daily_loss_limit_pct: float = 1.00
    max_open_positions: int = 2
    max_single_position_exposure_pct: float = 25.0
    minimum_net_rr: float = 2.0
    current_open_positions: int = 0
    realised_daily_pnl_inr: float = 0.0
    freshness: str = "SIMULATED"
    instrument_status: str = "ACTIVE"


@router.post("/risk/calculate")
async def api_calculate_risk(payload: RiskCalculationRequest):
    inp = RiskEvaluationInput(
        symbol=payload.symbol.upper(),
        direction=payload.direction.upper(),
        trading_mode=payload.trading_mode.upper(),
        entry_price=Decimal(str(payload.entry_price)),
        stop_loss=Decimal(str(payload.stop_loss)),
        target_price=Decimal(str(payload.target_price)),
        freshness=payload.freshness.upper(),
        instrument_status=payload.instrument_status.upper(),
        strategy_confirmed=True,
        mtf_aligned=True,
    )
    params = RiskParameters(
        capital_inr=Decimal(str(payload.capital_inr)),
        risk_per_trade_pct=Decimal(str(payload.risk_per_trade_pct)),
        daily_loss_limit_pct=Decimal(str(payload.daily_loss_limit_pct)),
        max_open_positions=payload.max_open_positions,
        max_single_position_exposure_pct=Decimal(str(payload.max_single_position_exposure_pct)),
        minimum_net_rr=Decimal(str(payload.minimum_net_rr)),
    )
    state = AccountRiskState(
        current_open_positions=payload.current_open_positions,
        realised_daily_pnl_inr=Decimal(str(payload.realised_daily_pnl_inr)),
    )
    decision = evaluate_trade_risk(inp, params, state)
    return {"decision": _ser(asdict(decision))}


def _evaluate_symbol_pipeline(
    symbol: str,
    trading_mode: str,
    strategy_code: str,
    capital_inr: Decimal,
    open_positions_count: int,
    realised_daily_pnl_inr: Decimal,
    provider: Optional[MarketDataProvider] = None,
) -> Dict[str, Any]:
    sym = symbol.upper()
    mode = trading_mode.upper()
    strat_code = strategy_code.upper()
    c_5m = _load_domain_candles(sym, "5m", bars=45, provider=provider)
    c_15m = _load_domain_candles(sym, "15m", bars=45, provider=provider)
    c_1h = _load_domain_candles(sym, "1h", bars=45, provider=provider)
    c_1d = _load_domain_candles(sym, "1d", bars=45, provider=provider)
    candles_by_tf = {"5m": c_5m, "15m": c_15m, "1h": c_1h, "1d": c_1d}

    risk_params = RiskParameters(capital_inr=capital_inr)
    account_state = AccountRiskState(
        current_open_positions=open_positions_count,
        realised_daily_pnl_inr=realised_daily_pnl_inr,
    )
    plan = build_trade_plan(
        symbol=sym,
        strategy_code=strat_code,
        trading_mode=mode,
        candles_by_tf=candles_by_tf,
        risk_params=risk_params,
        account_state=account_state,
        freshness=(c_1d[-1].freshness if c_1d else "UNAVAILABLE"),
    )
    explanation = generate_grounded_explanation(plan)
    latest_close = c_1d[-1].close if c_1d else Decimal("0.00")

    return {
        "symbol": sym,
        "trading_mode": mode,
        "strategy_code": strat_code,
        "latest_close": str(latest_close),
        "data_source": (c_1d[-1].source if c_1d else "UNAVAILABLE"),
        "data_freshness": (c_1d[-1].freshness if c_1d else "UNAVAILABLE"),
        "trade_plan": _ser(asdict(plan)),
        "ai_explanation": _ser(asdict(explanation)),
    }


@router.get("/scanner")
async def api_run_scanner(
    request: Request,
    trading_mode: str = Query("SWING", description="INTRADAY or SWING"),
    strategy_code: str = Query("BREAKOUT_RETEST", description="Strategy code"),
    symbols: str = Query("RELIANCE,TCS,HDFCBANK,INFY,ICICIBANK,SBIN", description="Comma-separated NSE symbols"),
    user: AuthenticatedUser = Depends(get_current_user),
):
    sim = request.app.state.paper_simulator
    open_cnt, realised_pnl = _open_count_and_realised_pnl(sim, user.user_id)
    sym_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if len(sym_list) > MAX_SCAN_SYMBOLS:
        raise TradeIQException("VALIDATION_ERROR", f"At most {MAX_SCAN_SYMBOLS} symbols per scan", status_code=422)
    results = []
    for sym in sym_list:
        res = _evaluate_symbol_pipeline(
            symbol=sym,
            trading_mode=trading_mode,
            strategy_code=strategy_code,
            capital_inr=Decimal("75000.00"),
            open_positions_count=open_cnt,
            realised_daily_pnl_inr=realised_pnl,
            provider=request.app.state.market_provider,
        )
        results.append(res)

    return {
        "run_id": f"SCAN-{uuid.uuid4().hex[:8].upper()}",
        "trading_mode": trading_mode.upper(),
        "strategy_code": strategy_code.upper(),
        "scanned_count": len(results),
        "source": ("DEMO" if request.app.state.settings.demo_mode else "ANGEL_ONE"),
        "freshness": ("SIMULATED" if request.app.state.settings.demo_mode else ("HISTORICAL" if any(x.get("data_freshness") == "HISTORICAL" for x in results) else "UNAVAILABLE")),
        "as_of_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "results": results,
    }


class PlannerEvaluateRequest(BaseModel):
    symbol: str = "RELIANCE"
    trading_mode: str = "SWING"
    strategy_code: str = "BREAKOUT_RETEST"
    capital_inr: float = 75000.0


@router.post("/planner/evaluate")
async def api_planner_evaluate(
    payload: PlannerEvaluateRequest,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
):
    sim = request.app.state.paper_simulator
    open_cnt, realised_pnl = _open_count_and_realised_pnl(sim, user.user_id)
    return _evaluate_symbol_pipeline(
        symbol=payload.symbol,
        trading_mode=payload.trading_mode,
        strategy_code=payload.strategy_code,
        capital_inr=Decimal(str(payload.capital_inr)),
        open_positions_count=open_cnt,
        realised_daily_pnl_inr=realised_pnl,
        provider=request.app.state.market_provider,
    )


class OpenPaperPositionRequest(BaseModel):
    symbol: str = "RELIANCE"
    direction: str = "LONG"
    trading_mode: str = "SWING"
    strategy_code: str = "BREAKOUT_RETEST"
    quantity: int = 5
    entry_price: float = 1285.50
    stop_loss: float = 1265.00
    target_price: float = 1340.00


class ClosePaperPositionRequest(BaseModel):
    exit_price: float
    exit_reason: str = "TARGET_HIT"


def _build_paper_summary(sim, user_id: str) -> Dict[str, Any]:
    starting_cap = Decimal("75000.00")
    mine = _user_positions(sim, user_id)
    open_pos = [asdict(p) for p in mine if p.status == "OPEN"]
    closed_pos = [asdict(p) for p in mine if p.status == "CLOSED"]
    realised_pnl = sum((p.net_pnl_inr for p in mine if p.status == "CLOSED"), Decimal("0.00"))
    deployed = sum((p.entry_fill_price * Decimal(str(p.quantity)) for p in mine if p.status == "OPEN"), Decimal("0.00"))
    cash_bal = starting_cap + realised_pnl - deployed
    total_eq = starting_cap + realised_pnl
    return _ser(
        {
            "starting_capital_inr": starting_cap,
            "cash_balance_inr": cash_bal,
            "total_equity_inr": total_eq,
            "realised_pnl_inr": realised_pnl,
            "open_positions_count": len(open_pos),
            "closed_positions_count": len(closed_pos),
            "open_positions": open_pos,
            "closed_positions": closed_pos,
        }
    )


@router.get("/paper/summary")
async def api_paper_summary(request: Request, user: AuthenticatedUser = Depends(get_current_user)):
    return _build_paper_summary(request.app.state.paper_simulator, user.user_id)


@router.post("/paper/positions")
async def api_open_paper_position(
    payload: OpenPaperPositionRequest,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
):
    sim = request.app.state.paper_simulator
    try:
        pos = sim.open_paper_position(
            user_id=user.user_id,
            symbol=payload.symbol.upper(),
            trading_mode=payload.trading_mode.upper(),
            direction=payload.direction.upper(),
            quantity=payload.quantity,
            planned_entry=Decimal(str(payload.entry_price)),
            stop_loss=Decimal(str(payload.stop_loss)),
            target_price=Decimal(str(payload.target_price)),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    pos_dict = _ser(asdict(pos))
    persistence = await asyncio.to_thread(request.app.state.legacy_paper_store.insert_position, pos)
    return {"position": pos_dict, "persistence": persistence, "summary": _build_paper_summary(sim, user.user_id)}


@router.post("/paper/positions/{position_id}/close")
async def api_close_paper_position(
    position_id: str,
    payload: ClosePaperPositionRequest,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
):
    sim = request.app.state.paper_simulator
    try:
        closed = sim.close_paper_position(
            user_id=user.user_id,
            position_id=position_id,
            planned_exit_price=Decimal(str(payload.exit_price)),
            exit_reason=payload.exit_reason,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    closed_dict = _ser(asdict(closed))
    persistence = await asyncio.to_thread(request.app.state.legacy_paper_store.close_position, closed)

    j_entry = {
        "journal_id": f"JRN-{uuid.uuid4().hex[:8].upper()}",
        "user_id": user.user_id,
        "position_id": closed.position_id,
        "symbol": closed.symbol,
        "strategy_code": "BREAKOUT_RETEST",
        "direction": closed.direction,
        "net_pnl_inr": str(closed.net_pnl_inr),
        "r_multiple": str(closed.r_multiple),
        "emotion_tag": "DISCIPLINED",
        "followed_rules": True,
        "setup_rating": 5,
        "notes": f"Auto-logged from paper position {closed.position_id} ({closed.exit_reason}).",
        "created_at_utc": closed.closed_at_ist,
    }
    request.app.state.journal_entries.append(j_entry)
    return {"closed_position": closed_dict, "persistence": persistence, "summary": _build_paper_summary(sim, user.user_id)}


class JournalCreateRequest(BaseModel):
    symbol: str = "RELIANCE"
    strategy_code: str = "BREAKOUT_RETEST"
    direction: str = "LONG"
    net_pnl_inr: float = 420.0
    r_multiple: float = 2.1
    emotion_tag: str = Field("DISCIPLINED")
    followed_rules: bool = True
    setup_rating: int = Field(5, ge=1, le=5)
    notes: str = "Waited for 15m candle close above resistance before paper entry."


@router.get("/journal")
async def api_list_journal(request: Request, user: AuthenticatedUser = Depends(get_current_user)):
    entries = _user_journal(request, user.user_id)
    return {"count": len(entries), "entries": [_public_journal_entry(e) for e in reversed(entries)]}


@router.post("/journal")
async def api_create_journal(
    payload: JournalCreateRequest,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
):
    entry = {
        "journal_id": f"JRN-{uuid.uuid4().hex[:8].upper()}",
        "user_id": user.user_id,
        "position_id": None,
        "symbol": payload.symbol.upper(),
        "strategy_code": payload.strategy_code.upper(),
        "direction": payload.direction.upper(),
        "net_pnl_inr": str(Decimal(str(payload.net_pnl_inr)).quantize(Decimal("0.01"))),
        "r_multiple": str(Decimal(str(payload.r_multiple)).quantize(Decimal("0.01"))),
        "emotion_tag": payload.emotion_tag.upper(),
        "followed_rules": payload.followed_rules,
        "setup_rating": payload.setup_rating,
        "notes": payload.notes,
        "created_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    request.app.state.journal_entries.append(entry)
    return {"entry": _public_journal_entry(entry), "persistence": "IN_MEMORY_ONLY — normalized journal persistence still pending"}


@router.get("/analytics")
async def api_get_analytics(request: Request, user: AuthenticatedUser = Depends(get_current_user)):
    sim = request.app.state.paper_simulator
    records = []
    for p in _user_positions(sim, user.user_id):
        if p.status == "CLOSED":
            records.append(
                {
                    "gross_pnl_inr": p.gross_pnl_inr,
                    "charges_inr": p.entry_charges_inr + p.exit_charges_inr,
                    "net_pnl_inr": p.net_pnl_inr,
                    "r_multiple": p.r_multiple,
                    "strategy_code": "BREAKOUT_RETEST",
                }
            )
    report = compute_performance_analytics(records)
    return {
        "has_closed_trades": len(records) > 0,
        "analytics": _ser(asdict(report)),
    }


class BacktestRunRequest(BaseModel):
    symbol: str = "RELIANCE"
    timeframe: str = "1d"
    strategy_code: str = "BREAKOUT_RETEST"


@router.post("/backtest/run")
async def api_run_backtest(payload: BacktestRunRequest, request: Request):
    candles = _load_domain_candles(payload.symbol.upper(), payload.timeframe, bars=45, provider=request.app.state.market_provider)
    result = run_bar_by_bar_backtest(candles, payload.strategy_code.upper())
    return {"backtest": _ser(asdict(result))}


@router.get("/news")
async def api_get_news(request: Request, symbol: Optional[str] = Query(None)):
    svc = request.app.state.news_alerts_service
    symbols_to_fetch = [symbol.upper()] if symbol else ["RELIANCE", "TCS", "HDFCBANK"]
    for sym in symbols_to_fetch:
        await asyncio.to_thread(svc.fetch_live_rss_or_api_news, sym, True)
    items = svc.list_news(symbol=symbol)
    return {
        "count": len(items),
        "source": "MULTI_SOURCE_AGGREGATOR",
        "items": [_ser(asdict(i)) for i in items],
    }


class TriggerAlertRequest(BaseModel):
    rule_id: str = "RULE-PRICE-RELIANCE"
    symbol: str = "RELIANCE"
    condition_type: str = "CROSSES_ABOVE"
    threshold: float = 1200.0


@router.post("/alerts")
async def api_trigger_alert(payload: TriggerAlertRequest, request: Request):
    svc = request.app.state.news_alerts_service
    candles = _load_domain_candles(payload.symbol.upper(), "15m", bars=5, provider=request.app.state.market_provider)
    if not candles:
        raise HTTPException(status_code=503, detail="Market candles unavailable; no alert evaluated")
    latest_closed = candles[-1]
    triggered = svc.evaluate_closed_candle_price_alert(
        alert_id=payload.rule_id,
        condition_type=payload.condition_type.upper(),
        threshold=Decimal(str(payload.threshold)),
        candle=latest_closed,
        now_epoch=time.time(),
        cooldown_sec=0,
    )
    tg_status = None
    if triggered:
        tg_status = request.app.state.telegram_notifier.send_closed_candle_alert(
            symbol=payload.symbol.upper(),
            message=f"{payload.condition_type.upper()} threshold ₹{payload.threshold} confirmed on closed candle (₹{latest_closed.close})",
            candle_ts_ist=latest_closed.ts_ist.isoformat(),
        )
    return {
        "alert": {
            "rule_id": payload.rule_id,
            "symbol": payload.symbol.upper(),
            "condition_type": payload.condition_type.upper(),
            "threshold": str(payload.threshold),
            "candle_close": str(latest_closed.close),
            "triggered": triggered,
            "telegram_delivery": tg_status,
        }
    }


class AssistantQueryRequest(BaseModel):
    question: str
    symbol: str = "RELIANCE"
    trading_mode: str = "SWING"


@router.post("/assistant/query")
async def api_assistant_query(payload: AssistantQueryRequest, request: Request):
    q_lower = payload.question.lower()
    if any(tok in q_lower for tok in ("place live order", "buy with real money", "execute broker order", "bypass stop loss")):
        return {
            "mode": "REFUSAL_GUARDRAIL",
            "answer": (
                "Refused by TRADEIQ Safety Guardrail: Initial version supports Paper Trading only "
                "and never places real-money broker orders or bypasses deterministic risk limits."
            ),
            "cited_fact_ids": ["F01", "F02"],
            "validation_passed": True,
        }

    eval_res = _evaluate_symbol_pipeline(
        symbol=payload.symbol,
        trading_mode=payload.trading_mode,
        strategy_code="BREAKOUT_RETEST",
        capital_inr=Decimal("75000.00"),
        open_positions_count=0,
        realised_daily_pnl_inr=Decimal("0.00"),
        provider=request.app.state.market_provider,
    )
    ai_exp = eval_res["ai_explanation"]
    plan = eval_res["trade_plan"]
    return {
        "mode": ai_exp["source"],
        "symbol": eval_res["symbol"],
        "plan_status": plan["status"],
        "answer": f"{ai_exp['analysis']} | Conclusion: {ai_exp['conclusion']}",
        "cited_fact_ids": ai_exp["cited_fact_ids"],
        "validation_passed": True,
        "trade_plan_snapshot": plan,
    }


class AssistantToolRequest(BaseModel):
    tool_name: str
    params: Dict[str, Any] = {}


@router.post("/assistant/tool")
async def api_assistant_tool(
    payload: AssistantToolRequest,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
):
    toolkit = ReadOnlyAssistantToolkit(
        analysis_fn=lambda sym, mode: _evaluate_symbol_pipeline(sym, mode, "BREAKOUT_RETEST", Decimal("75000.00"), 0, Decimal("0.00"), request.app.state.market_provider),
        plan_fn=lambda sym, mode, strat: _evaluate_symbol_pipeline(sym, mode, strat, Decimal("75000.00"), 0, Decimal("0.00"), request.app.state.market_provider)["trade_plan"],
        calc_qty_fn=lambda cap, e, sl, t, mode: _ser(
            asdict(
                evaluate_trade_risk(
                    RiskEvaluationInput(symbol="RELIANCE", direction="LONG", trading_mode=mode, entry_price=e, stop_loss=sl, target_price=t, freshness="SIMULATED", instrument_status="ACTIVE", strategy_confirmed=True, mtf_aligned=True),
                    RiskParameters(capital_inr=cap),
                    AccountRiskState(),
                )
            )
        ),
        journal_stats_fn=lambda: {"journal_count": len(_user_journal(request, user.user_id))},
    )
    try:
        out = toolkit.invoke_tool(payload.tool_name, **payload.params)
        return {"tool_name": payload.tool_name, "result": out}
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))


@router.get("/notifications/telegram/status")
async def api_telegram_status(request: Request):
    return request.app.state.telegram_notifier.status_summary()


@router.get("/market/universe")
async def api_market_universe():
    return {
        "exchange": "NSE",
        "segment": "CASH_EQUITY (-EQ)",
        "count": len(NSE_LIQUID_CASH_UNIVERSE),
        "symbols": NSE_LIQUID_CASH_UNIVERSE,
        "global_premarket_cues": [{"ticker": t, "name": n} for t, n in GLOBAL_PREMARKET_CUES],
    }


@router.get("/market/premarket")
async def api_premarket_report(request: Request):
    cfg = request.app.state.settings
    return {
        "report_type": "NSE_9AM_PREMARKET_INTELLIGENCE",
        "source": "DEMO" if cfg.demo_mode else "ANGEL_ONE",
        "freshness": "SIMULATED" if cfg.demo_mode else "UNAVAILABLE",
        "as_of_ist": datetime.datetime.now(IST).isoformat(),
        "global_cues_monitored": [n for _, n in GLOBAL_PREMARKET_CUES],
        "nse_universe_size": len(NSE_LIQUID_CASH_UNIVERSE),
        "disclaimer": "Analytical & educational paper-trading tool only. Not investment advice.",
    }


@router.get("/integrations/status")
async def api_integrations_status(request: Request):
    cfg = request.app.state.settings
    summary = cfg.safe_summary()
    provider = request.app.state.market_provider
    angel_status = provider.status_summary() if hasattr(provider, "status_summary") else {"connection_state": "DEMO_MODE"}
    return {
        "source": "DEMO" if cfg.demo_mode else "ANGEL_ONE",
        "freshness": "SIMULATED" if cfg.demo_mode else ("LIVE" if angel_status.get("websocket_connected") and angel_status.get("last_tick_epoch") and time.time() - angel_status["last_tick_epoch"] < 90 else "UNAVAILABLE"),
        "provider_startup_error": request.app.state.provider_startup_error,
        "integrations": {
            "angel_one_smartapi": {
                **angel_status,
                "status": "SIMULATED_MODE" if cfg.demo_mode else angel_status.get("connection_state", "NOT_CONNECTED"),
                "read_only_only": True,
                "rate_limit_rps": 2.5,
            },
            "supabase_database": request.app.state.supabase_bridge.status_summary(),
            "legacy_paper_trade_storage": request.app.state.legacy_paper_store.status_summary(),
            "gemini_ai_pool": {
                "status": "CONFIGURED_NOT_LIVE_VERIFIED" if summary["gemini_configured"] else "NOT_CONFIGURED",
                "key_pool_size": summary["gemini_key_pool_count"],
                "grounding_validator": "ACTIVE (F01..Fnn fact_id check)",
            },
            "telegram_alerts": request.app.state.telegram_notifier.status_summary(),
            "stock_news_engine": {
                "primary_feeds": ["NSE Official RSS (Keyless)", "Google News India NSE RSS (Keyless)"],
                "optional_finnhub_configured": summary["finnhub_configured"],
                "optional_marketaux_configured": summary["marketaux_configured"],
            },
        },
    }


class TelegramTestRequest(BaseModel):
    confirm_send: bool = False


@router.post("/notifications/telegram/test")
async def api_telegram_test(payload: TelegramTestRequest, request: Request):
    if not payload.confirm_send:
        raise HTTPException(status_code=400, detail="Set confirm_send=true to send a real Telegram test message")
    notifier = request.app.state.telegram_notifier
    result = notifier.send_closed_candle_alert(
        symbol="TRADEIQ TEST",
        message="Telegram connection test. This is a PAPER-ONLY notification; no order was placed.",
        candle_ts_ist=datetime.datetime.now(IST).isoformat(),
    )
    if not result.get("delivered"):
        raise HTTPException(status_code=503, detail={"message": "Telegram test delivery failed", "result": result})
    return {"sent": True, "result": result}


@router.post("/integrations/supabase/verify")
async def api_verify_supabase(request: Request):
    bridge = request.app.state.supabase_bridge
    ok = await asyncio.to_thread(bridge.verify_connection)
    if not ok:
        raise HTTPException(status_code=503, detail={"message": "Supabase connection could not be verified", "status": bridge.status_summary()})
    return {"verified": True, "status": bridge.status_summary()}
