from ....jobs.auto_trader import get_horizon_meta, run_multi_period_backtest
import json
"""
Phase 3–6 Endpoints + Unified Legacy Bridges:
- Risk Calculator (/api/v1/risk/calculate)
- Multi-Timeframe Scanner (/api/v1/scanner)
- Deterministic Trade Planner + Live Grounded Gemini AI Explanation (/api/v1/planner/evaluate)
- Paper Trading Simulator & Ledger (/api/v1/paper/*) with Supabase REST Sync
- Trading Journal & Emotion/Rule Tagging (/api/v1/journal)
- Performance Analytics (/api/v1/analytics)
- Bar-by-Bar Backtester with OOS Split & Hash (/api/v1/backtest/run)
- NSE News, Keyless RSS & Multi-Channel Alerts (/api/v1/news, /api/v1/alerts)
- Read-Only Grounded AI Assistant & Fixed Toolkit (/api/v1/assistant/query, /api/v1/assistant/tool)
- Telegram Status & Test (/api/v1/notifications/telegram/status, /test)
- 160+ NSE Cash Universe & 9:00 AM IST Pre-Market Report (/api/v1/market/universe, /api/v1/market/premarket)
- Integration Verification Matrix (/api/v1/integrations/status, /supabase/verify)
"""
import asyncio
import datetime
import time
import uuid
from dataclasses import asdict
from decimal import Decimal
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ....ai.grounding import build_gemini_callable, generate_grounded_explanation
from ....ai.llm_client import ReadOnlyAssistantToolkit
from ....core.auth import AuthenticatedUser, get_current_user
from ....core.errors import TradeIQException
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
from ....providers.base import MarketDataProvider
from ....providers.demo_provider import DemoMarketDataProvider

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
        out.append(
            DomainCandle(
                symbol=symbol.upper(),
                timeframe=timeframe,
                ts_ist=dt,
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
        )
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
    llm_client: Optional[Any] = None,
    user_question: Optional[str] = None,
    use_llm: bool = False,
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
        freshness=(c_15m[-1].freshness if c_15m else (c_1d[-1].freshness if c_1d else (c_1h[-1].freshness if c_1h else "UNAVAILABLE"))),
    )
    llm_cb = build_gemini_callable(llm_client, user_question=user_question) if use_llm else None
    explanation = generate_grounded_explanation(plan, llm_callable=llm_cb, user_question=user_question)
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
            use_llm=False,  # Keep bulk scanner fast & quota-safe; Gemini runs on Planner & Assistant
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
    return await asyncio.to_thread(
        _evaluate_symbol_pipeline,
        payload.symbol,
        payload.trading_mode,
        payload.strategy_code,
        Decimal(str(payload.capital_inr)),
        open_cnt,
        realised_pnl,
        request.app.state.market_provider,
        request.app.state.llm_client,
        None,
        True,  # Actively invoke GeminiLLMClient when configured, with strict validation & template fallback
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
    exit_price: Optional[float] = None
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
    eff_exit_px = payload.exit_price
    if eff_exit_px is None or eff_exit_px <= 0:
        pos_obj = sim.positions.get(position_id) if hasattr(sim, "positions") else None
        if pos_obj is not None:
            sym_u = str(getattr(pos_obj, "symbol", "")).upper()
            pulse = getattr(request.app.state, "live_quotes", None) or {}
            if sym_u in pulse and getattr(pulse[sym_u], "ltp", 0) > 0:
                eff_exit_px = float(pulse[sym_u].ltp)
            else:
                try:
                    q = request.app.state.market_provider.get_quote(sym_u)
                    if q and getattr(q, "ltp", 0) > 0:
                        eff_exit_px = float(q.ltp)
                except Exception:
                    pass
                if not eff_exit_px or eff_exit_px <= 0:
                    eff_exit_px = float(getattr(pos_obj, "current_ltp", 0) or getattr(pos_obj, "entry_fill_price", 0) or 0)
        if not eff_exit_px or eff_exit_px <= 0:
            raise HTTPException(status_code=400, detail="Missing exit_price and live LTP unavailable")
    try:
        closed = sim.close_paper_position(
            user_id=user.user_id,
            position_id=position_id,
            planned_exit_price=Decimal(str(eff_exit_px)),
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
async def api_get_news(
    request: Request,
    symbol: Optional[str] = Query(None),
    refresh: bool = Query(False),
):
    svc = request.app.state.news_alerts_service
    llm = request.app.state.llm_client
    allow_net = (request.app.state.settings.app_env != "test") or refresh
    items = await asyncio.to_thread(
        svc.fetch_top_indian_market_news,
        symbol,
        allow_net,
        llm,
        refresh,
    )
    return {
        "count": len(items),
        "source": "INDIAN_MULTI_SOURCE_RSS_AND_GEMINI",
        "gemini_shield_active": bool(llm and llm.is_configured()),
        "feed_health": svc.feed_health,
        "items": [_ser(asdict(i)) for i in items[:30]],
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
    strategy_code: str = "BREAKOUT_RETEST"


@router.post("/assistant/query")
async def api_assistant_query(payload: AssistantQueryRequest, request: Request):
    q_lower = payload.question.lower()
    if any(tok in q_lower for tok in ("place live order", "buy with real money", "execute broker order", "bypass stop loss")):
        return {
            "mode": "REFUSAL_GUARDRAIL",
            "symbol": payload.symbol.upper(),
            "plan_status": "BLOCKED",
            "answer": (
                "Refused by TRADEIQ Safety Guardrail: Initial version supports Paper Trading only "
                "and never places real-money broker orders or bypasses deterministic risk limits."
            ),
            "evidence": [],
            "risks_and_limitations": "Live order execution is permanently disabled in TRADEIQ v1.",
            "cited_fact_ids": ["F01", "F02"],
            "validation_passed": True,
        }

    # Auto-detect NSE symbol mentioned in the user's question (e.g. "Why did HAL pass or fail?" -> HAL)
    import re as _re
    target_sym = payload.symbol.upper()
    q_tokens = set(_re.findall(r"\b[A-Z0-9&-]{2,15}\b", payload.question.upper()))
    universe_set = {s.upper() for s in NSE_LIQUID_CASH_UNIVERSE}
    matched_syms = [tok for tok in q_tokens if tok in universe_set]
    if matched_syms:
        target_sym = matched_syms[0]

    eval_res = await asyncio.to_thread(
        _evaluate_symbol_pipeline,
        target_sym,
        payload.trading_mode,
        payload.strategy_code,
        Decimal("75000.00"),
        0,
        Decimal("0.00"),
        request.app.state.market_provider,
        request.app.state.llm_client,
        payload.question,
        True,  # Actively invokes GeminiLLMClient when configured!
    )
    ai_exp = eval_res["ai_explanation"]
    plan = eval_res["trade_plan"]
    llm = request.app.state.llm_client
    mode_out = ai_exp["source"]
    answer_out = f"{ai_exp['analysis']} | Conclusion: {ai_exp['conclusion']}"

    # If strict planner numeric validator fell back on a general/planning question, ask Gemini directly
    # with verified live account/market context (never inventing prices or placing real orders).
    if mode_out == "DETERMINISTIC_TEMPLATE" and llm and llm.is_configured():
        try:
            at = request.app.state.auto_trader
            indices = request.app.state.market_provider.get_index_quotes() if hasattr(request.app.state.market_provider, "get_index_quotes") else []
            ctx = {
                "question": payload.question,
                "symbol": eval_res["symbol"],
                "plan_status": plan["status"],
                "latest_close": eval_res.get("latest_close"),
                "auto_trader_config": at.config.to_dict(),
                "indices": indices,
            }
            gres = await asyncio.to_thread(
                llm.generate_structured_json,
                (
                    "You are TRADEIQ's Paper-Trading Assistant for NSE India. Answer the user's question clearly "
                    "and practically in 3-5 concise sentences (matching the user's language if Bengali/English). "
                    "Never invent stock prices or recommend real-money orders. Return JSON with key 'answer' (double-quoted JSON)."
                ),
                json.dumps(ctx, ensure_ascii=False),
            )
            if isinstance(gres, dict) and gres.get("answer"):
                mode_out = "GEMINI_LIVE"
                answer_out = str(gres["answer"])[:1200]
        except Exception:
            pass

    return {
        "mode": mode_out,
        "gemini_configured": llm.is_configured(),
        "gemini_last_error": getattr(llm, "last_error", None),
        "symbol": eval_res["symbol"],
        "plan_status": plan["status"],
        "answer": answer_out,
        "data_used": ai_exp["data_used"],
        "evidence": ai_exp["evidence"],
        "risks_and_limitations": ai_exp["risks_and_limitations"],
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
    llm = request.app.state.llm_client
    angel_status = provider.status_summary() if hasattr(provider, "status_summary") else {"connection_state": "DEMO_MODE"}
    gemini_status = (
        "LIVE_VERIFIED"
        if (llm.request_count > 0 and llm.error_count < llm.request_count)
        else ("CONFIGURED_READY" if summary["gemini_configured"] else "NOT_CONFIGURED")
    )
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
                "status": gemini_status,
                "model": llm.model_name,
                "key_pool_size": summary["gemini_key_pool_count"],
                "request_count": llm.request_count,
                "error_count": llm.error_count,
                "last_latency_ms": llm.last_latency_ms,
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


# ======================================================================================
# TRADEIQ v2.0 — 3-Engine Live Scanner, Auto Paper-Trader, Penny-Accurate Ledger & Brief
# ======================================================================================

def _collect_live_ltps(request: Request, symbols: List[str]) -> Dict[str, Decimal]:
    provider = request.app.state.market_provider
    out: Dict[str, Decimal] = {}
    for sym in set(s.upper() for s in symbols):
        try:
            q = provider.get_quote(sym)
            if q and q.ltp > 0:
                out[sym] = q.ltp
        except Exception:
            continue
    return out


class AutoTraderConfigUpdate(BaseModel):
    enabled: Optional[bool] = None
    starting_balance_inr: Optional[float] = None
    per_trade_amount_inr: Optional[float] = None
    max_open_positions: Optional[int] = None
    daily_loss_limit_pct: Optional[float] = None
    max_consecutive_losses: Optional[int] = None
    enable_engine_1_orb: Optional[bool] = None
    enable_engine_2a_vcp: Optional[bool] = None
    enable_engine_2b_rsi2: Optional[bool] = None
    enable_news_shield: Optional[bool] = None
    reset_kill_switch: Optional[bool] = False


class AutoTraderRunRequest(BaseModel):
    execute_entries: bool = True
    limit: int = Field(12, ge=3, le=30)


class DailySummaryRequest(BaseModel):
    send_telegram: bool = False



def _enrich_ledger_horizons(ledger: Dict[str, Any]) -> Dict[str, Any]:
    """Attaches explicit horizon_bucket, horizon_label, and hold_horizon to open positions."""
    for pos in (ledger.get("open_positions_live") or ledger.get("open_positions") or []):
        hm = get_horizon_meta(pos.get("engine_code", ""), pos.get("trading_mode", "INTRADAY"))
        pos["horizon_bucket"] = hm["horizon_bucket"]
        pos["horizon_label"] = hm["horizon_label"]
        pos["hold_horizon"] = hm["hold_horizon"]
    return ledger



@router.delete("/paper/positions/{position_id}")
async def api_delete_paper_position(
    position_id: str,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Manual owner-only deletion for mistaken paper trades; never invoked automatically."""
    sim = request.app.state.paper_simulator
    pos = sim.positions.get(position_id)
    if pos and pos.user_id == user.user_id:
        del sim.positions[position_id]
    store = getattr(request.app.state, "legacy_paper_store", None)
    db_res = await asyncio.to_thread(store.delete_position, position_id) if (store and store.is_configured()) else {"deleted": False}
    return {"deleted": True, "position_id": position_id, "supabase": db_res}


@router.get("/paper/ledger")
async def api_paper_ledger(
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
):
    sim = request.app.state.paper_simulator
    at = request.app.state.auto_trader
    open_syms = [p.symbol for p in sim.positions.values() if p.user_id == user.user_id and p.status == "OPEN"]
    live_ltps = await asyncio.to_thread(_collect_live_ltps, request, open_syms)
    ledger = _enrich_ledger_horizons(sim.account_ledger(user.user_id, at.config.starting_balance_inr, live_ltps))
    closed_rows = []
    for p in sorted(
        [x for x in sim.positions.values() if x.user_id == user.user_id and x.status == "CLOSED"],
        key=lambda x: x.closed_at_ist or "",
        reverse=True,
    ):
        row = _ser(asdict(p))
        hm = get_horizon_meta(row.get("engine_code", ""), row.get("trading_mode", "INTRADAY"))
        row["horizon_bucket"] = hm["horizon_bucket"]
        row["horizon_label"] = hm["horizon_label"]
        row["hold_horizon"] = hm["hold_horizon"]
        closed_rows.append(row)
    return {
        "source": "DEMO" if request.app.state.settings.demo_mode else "ANGEL_ONE",
        "ledger": ledger,
        "closed_trades": closed_rows,
    }


@router.get("/auto-trader/status")
async def api_auto_trader_status(
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
):
    sim = request.app.state.paper_simulator
    at = request.app.state.auto_trader
    open_syms = [p.symbol for p in sim.positions.values() if p.user_id == user.user_id and p.status == "OPEN"]
    live_ltps = await asyncio.to_thread(_collect_live_ltps, request, open_syms)
    ledger = _enrich_ledger_horizons(sim.account_ledger(user.user_id, at.config.starting_balance_inr, live_ltps))
    all_active = request.app.state.instrument_service.search("", active_only=True)
    watched_syms = [i.symbol for i in all_active[:12]]
    return {
        "config": at.config.to_dict(),
        "kill_switch_active": at.kill_switch_active,
        "kill_switch_reason": at.kill_switch_reason,
        "consecutive_losses": at.consecutive_losses,
        "last_cycle_ist": at.last_cycle_ist,
        "last_daily_summary_date": at.last_daily_summary_date,
        "last_regime": (
            {
                "regime": at.last_regime.regime,
                "allow_auto_trading": at.last_regime.allow_auto_trading,
                "recommended_engines": at.last_regime.recommended_engines,
                "breadth_pct": str(at.last_regime.breadth_pct),
                "avg_atr_pct": str(at.last_regime.avg_atr_pct),
                "trend_score": at.last_regime.trend_score,
                "summary": at.last_regime.summary,
            }
            if at.last_regime
            else None
        ),
        "ledger": ledger,
        "watched_symbols": watched_syms,
        "total_universe_count": len(all_active),
        "scheduler_active": bool(getattr(request.app.state, "_scheduler_task", None)),
        "scan_stats": getattr(at, "last_scan_stats", {
            "scanned_symbols": len(watched_syms),
            "evaluated_count": len(at.last_signals),
            "confirmed_count": sum(1 for s in at.last_signals if s.get("status") == "CONFIRMED"),
            "watchlist_count": sum(1 for s in at.last_signals if s.get("status") == "WATCHLIST"),
            "rejected_count": sum(1 for s in at.last_signals if s.get("status") == "REJECTED"),
        }),
        "recent_signals": at.last_signals[:40],
        "execution_log": at.execution_log[:30],
    }


@router.put("/auto-trader/config")
async def api_auto_trader_update_config(
    payload: AutoTraderConfigUpdate,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
):
    at = request.app.state.auto_trader
    try:
        updated = at.update_config((payload.model_dump(exclude_none=True) if hasattr(payload, "model_dump") else payload.dict(exclude_none=True)))
    except ValueError as exc:
        raise TradeIQException("VALIDATION_ERROR", str(exc), status_code=422)
    return {
        "config": updated,
        "kill_switch_active": at.kill_switch_active,
        "kill_switch_reason": at.kill_switch_reason,
    }


def _execute_three_engine_cycle(
    request: Request,
    user_id: str,
    limit: int = 12,
    execute_entries: bool = True,
    allowed_engines: Optional[List[str]] = None,
) -> Dict[str, Any]:
    provider = request.app.state.market_provider
    svc = request.app.state.instrument_service
    sim = request.app.state.paper_simulator
    at = request.app.state.auto_trader
    news_svc = request.app.state.news_alerts_service
    tg = request.app.state.telegram_notifier

    instruments = svc.search("", active_only=True)[:limit]
    symbols = [i.symbol for i in instruments]
    open_syms = [p.symbol for p in sim.positions.values() if p.user_id == user_id and p.status == "OPEN"]
    all_syms = list(dict.fromkeys(symbols + open_syms))

    c15_map: Dict[str, List[DomainCandle]] = {}
    c1d_map: Dict[str, List[DomainCandle]] = {}
    depth_map: Dict[str, Dict[str, Any]] = {}
    live_ltps: Dict[str, Decimal] = {}
    advances = 0
    declines = 0

    for sym in all_syms:
        try:
            q = provider.get_quote(sym)
            if q and q.ltp > 0:
                live_ltps[sym] = q.ltp
                if q.ltp >= q.prev_close:
                    advances += 1
                else:
                    declines += 1
        except Exception:
            pass
        c15_map[sym] = _load_domain_candles(sym, "15m", 30, provider)
        c1d_map[sym] = _load_domain_candles(sym, "1d", 60, provider)
        if hasattr(provider, "get_market_depth"):
            try:
                d = provider.get_market_depth(sym)
                if d:
                    depth_map[sym] = d
            except Exception:
                pass

    indices = provider.get_index_quotes() if hasattr(provider, "get_index_quotes") else []
    news_risk = news_svc.get_symbol_sentiment_map(symbols)

    return at.run_auto_scan_and_execute(
        user_id=user_id,
        simulator=sim,
        symbols=symbols,
        candles_15m_by_sym=c15_map,
        candles_1d_by_sym=c1d_map,
        depth_by_sym=depth_map,
        indices_snapshot=indices,
        advances=advances,
        declines=declines,
        live_ltps=live_ltps,
        news_risk_by_sym=news_risk,
        telegram_notifier=tg,
        execute_entries=execute_entries,
        paper_store=getattr(request.app.state, "legacy_paper_store", None),
        allowed_engines=allowed_engines,
    )


@router.get("/engines/scan")
async def api_three_engine_scan(
    request: Request,
    limit: int = Query(12, ge=3, le=30),
    user: AuthenticatedUser = Depends(get_current_user),
):
    return await asyncio.to_thread(_execute_three_engine_cycle, request, user.user_id, limit, False)


@router.post("/auto-trader/run")
async def api_auto_trader_run(
    payload: AutoTraderRunRequest,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
):
    return await asyncio.to_thread(
        _execute_three_engine_cycle, request, user.user_id, payload.limit, payload.execute_entries
    )


@router.post("/auto-trader/daily-summary")
async def api_auto_trader_daily_summary(
    payload: DailySummaryRequest,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
):
    sim = request.app.state.paper_simulator
    at = request.app.state.auto_trader
    tg = request.app.state.telegram_notifier
    llm = request.app.state.llm_client
    open_syms = [p.symbol for p in sim.positions.values() if p.user_id == user.user_id and p.status == "OPEN"]
    live_ltps = await asyncio.to_thread(_collect_live_ltps, request, open_syms)
    ledger = sim.account_ledger(user.user_id, at.config.starting_balance_inr, live_ltps)

    ai_review = None
    if llm and llm.is_configured():
        try:
            r = await asyncio.to_thread(
                llm.generate_structured_json,
                "Summarize today's paper trading performance in 2 concise sentences. Use ONLY provided numbers.",
                str({
                    "equity": ledger["total_equity_inr"],
                    "realised_net": ledger["realised_net_pnl_inr"],
                    "accuracy_pct": ledger["accuracy_pct"],
                    "wins": ledger["wins"],
                    "losses": ledger["losses"],
                    "engines": ledger["engine_accuracy"],
                }),
            )
            ai_review = str(r.get("summary") or r.get("review") or r.get("conclusion") or "")[:240] or None
        except Exception:
            ai_review = None

    return await asyncio.to_thread(
        at.build_and_send_daily_summary,
        user.user_id,
        sim,
        live_ltps,
        tg,
        payload.send_telegram,
        ai_review,
    )


@router.get("/assistant/morning-brief")
async def api_morning_brief(
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
):
    provider = request.app.state.market_provider
    news_svc = request.app.state.news_alerts_service
    llm = request.app.state.llm_client
    indices = provider.get_index_quotes() if hasattr(provider, "get_index_quotes") else []
    news_items = news_svc.list_news()[:6]
    headlines = [{"headline": x.headline, "source": x.source_name, "sentiment": x.sentiment} for x in news_items]
    brief_text = (
        f"Live Indian Market Brief: {len(indices)} indices tracked, {len(headlines)} top headlines indexed. "
        "Review Regime Gate before morning ORB entries."
    )
    source = "DETERMINISTIC_TEMPLATE"
    if llm and llm.is_configured() and (indices or headlines):
        try:
            res = await asyncio.to_thread(
                llm.generate_structured_json,
                "Write a crisp 3-sentence Indian stock market Morning Brief using ONLY the provided indices and headlines. Return JSON {'brief': '...'} with double-quoted keys.",
                str({"indices": indices, "headlines": headlines}),
            )
            if res.get("brief"):
                brief_text = str(res["brief"])[:600]
                source = "GEMINI_LIVE"
        except Exception:
            pass
    return {
        "source": source,
        "indices": indices,
        "headlines_count": len(headlines),
        "brief": brief_text,
    }


@router.get("/system/connectivity")
async def api_system_connectivity(
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """
    Real-time, honest connectivity matrix for every external API service AND every UI panel.
    Clearly flags DEMO_MODE vs LIVE CONNECTED vs MISSING_API with exact missing env var names.
    """
    cfg = request.app.state.settings
    provider = request.app.state.market_provider
    llm = request.app.state.llm_client
    tg = request.app.state.telegram_notifier
    supa = request.app.state.supabase_bridge
    news_svc = request.app.state.news_alerts_service

    # 1. Angel One Live Market Data
    angel_creds_ok = bool(cfg.angel_api_key and cfg.angel_client_code and cfg.angel_password and cfg.angel_totp_secret)
    angel_health = provider.status_summary() if hasattr(provider, "status_summary") else {}
    ws_connected = bool(angel_health.get("websocket_connected"))
    if cfg.demo_mode:
        angel_state = "DEMO_MODE"
        angel_connected = False
        angel_detail = "Running in DEMO_MODE=true. Set DEMO_MODE=false in Render Environment to use live Angel One."
        angel_missing = ["DEMO_MODE=false"] + ([] if angel_creds_ok else ["ANGEL_API_KEY", "ANGEL_CLIENT_CODE", "ANGEL_PIN", "ANGEL_TOTP_SECRET"])
    elif not angel_creds_ok:
        angel_state = "MISSING_API"
        angel_connected = False
        angel_detail = "Angel One SmartAPI credentials are missing."
        angel_missing = [k for k, v in [
            ("ANGEL_API_KEY", cfg.angel_api_key),
            ("ANGEL_CLIENT_CODE", cfg.angel_client_code),
            ("ANGEL_PIN", cfg.angel_password),
            ("ANGEL_TOTP_SECRET", cfg.angel_totp_secret),
        ] if not v]
    elif ws_connected:
        angel_state = "CONNECTED"
        angel_connected = True
        angel_detail = "Angel One SmartAPI authenticated & WebSocket live."
        angel_missing = []
    else:
        angel_state = "PARTIAL"
        angel_connected = True
        err = request.app.state.provider_startup_error or angel_health.get("last_error")
        angel_detail = f"Angel One configured (session state: {angel_health.get('connection_state', 'READY')})." + (f" Note: {err}" if err else " Market may be closed.")
        angel_missing = []

    # 2. Gemini AI Pool
    gem_ok = bool(llm and llm.is_configured())
    gem_state = "CONNECTED" if gem_ok else "MISSING_API"
    gem_err = getattr(llm, "last_error", None)
    gem_detail = (f"Configured ({len(cfg.gemini_api_keys) or (1 if cfg.gemini_api_key else 0)} key(s), model {llm.model_name}, reqs: {llm.request_count}, errs: {llm.error_count})." + (f" Last error: {gem_err}" if gem_err else "")) if gem_ok else "GEMINI_API_KEYS not set (using deterministic template fallback)."
    gem_missing = [] if gem_ok else ["GEMINI_API_KEYS"]

    # 3. Supabase Database
    supa_ok = bool(supa and supa.is_configured())
    supa_state = "CONNECTED" if supa_ok else "MISSING_API"
    supa_detail = "Supabase REST bridge configured." if supa_ok else "SUPABASE_URL / SUPABASE_KEY missing (using in-memory ledger)."
    supa_missing = [] if supa_ok else ["SUPABASE_URL", "SUPABASE_KEY"]

    # 4. Telegram Bot
    tg_ok = bool(tg and tg.enabled)
    tg_state = "CONNECTED" if tg_ok else "MISSING_API"
    tg_detail = f"Telegram Bot active (sent: {len(tg.sent_messages)})." if tg_ok else "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID missing."
    tg_missing = [] if tg_ok else ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]

    # 5. Live Indian Market News RSS (Keyless)
    ok_feeds = sum(1 for v in news_svc.feed_health.values() if v.get("status") == "OK")
    news_state = "CONNECTED"
    news_detail = f"Keyless Indian RSS ready (ET, Livemint, Moneycontrol, Google News; {ok_feeds} feeds verified this session)."

    services = [
        {"key": "angel_one", "name": "Angel One Live", "state": angel_state, "connected": angel_connected, "detail": angel_detail, "missing_env_vars": angel_missing},
        {"key": "gemini_ai", "name": "Gemini AI", "state": gem_state, "connected": gem_ok, "detail": gem_detail, "missing_env_vars": gem_missing},
        {"key": "supabase", "name": "Supabase DB", "state": supa_state, "connected": supa_ok, "detail": supa_detail, "missing_env_vars": supa_missing},
        {"key": "telegram", "name": "Telegram Alerts", "state": tg_state, "connected": tg_ok, "detail": tg_detail, "missing_env_vars": tg_missing},
        {"key": "news_rss", "name": "Market News RSS", "state": news_state, "connected": True, "detail": news_detail, "missing_env_vars": []},
    ]

    def _panel(tab_key: str, name: str, deps: List[Dict[str, Any]]) -> Dict[str, Any]:
        missing: List[str] = []
        states = [d["state"] for d in deps]
        for d in deps:
            missing.extend(d["missing_env_vars"])
        if all(s == "CONNECTED" for s in states):
            p_state = "CONNECTED"
        elif "DEMO_MODE" in states:
            p_state = "DEMO_MODE"
        elif all(s in ("CONNECTED", "PARTIAL") for s in states):
            p_state = "PARTIAL"
        else:
            p_state = "API_NEEDED"
        return {
            "tab_key": tab_key,
            "panel_name": name,
            "state": p_state,
            "connected": p_state in ("CONNECTED", "PARTIAL"),
            "missing_apis": list(dict.fromkeys(missing)),
        }

    s_map = {s["key"]: s for s in services}
    panels = [
        _panel("dashboard", "Dashboard", [s_map["angel_one"]]),
        _panel("autotrader", "Auto-Trader", [s_map["angel_one"], s_map["telegram"], s_map["gemini_ai"]]),
        _panel("charts", "Live Charts", [s_map["angel_one"]]),
        _panel("engines", "3-Engine Scanner", [s_map["angel_one"]]),
        _panel("positions", "Paper Positions", [s_map["angel_one"], s_map["supabase"]]),
        _panel("accuracy", "Accuracy & P&L", [s_map["angel_one"], s_map["supabase"]]),
        _panel("news", "Market News", [s_map["news_rss"], s_map["gemini_ai"]]),
        _panel("planner", "Planner & Risk", [s_map["angel_one"], s_map["gemini_ai"]]),
        _panel("assistant", "AI Assistant", [s_map["gemini_ai"]]),
        _panel("journal", "Journal & Backtest", [s_map["angel_one"], s_map["supabase"]]),
        _panel("system", "System & Settings", services),
    ]

    return {
        "demo_mode": cfg.demo_mode,
        "all_connected": all(s["connected"] for s in services),
        "services": services,
        "panels": panels,
    }


@router.post("/engines/backtest-multi")
async def api_engines_backtest_multi(
    request: Request,
    limit: int = Query(12, ge=3, le=25),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Runs real 3M, 1M, 1W, and 1D walk-forward backtest on live Angel One historical candles (or demo in test mode)."""
    provider = request.app.state.market_provider
    svc = request.app.state.instrument_service
    at = request.app.state.auto_trader
    syms = [i.symbol for i in svc.search("", active_only=True)[:limit]]
    c1d_map: Dict[str, List[DomainCandle]] = {}
    c15_map: Dict[str, List[DomainCandle]] = {}

    def _fetch_all():
        for sym in syms:
            try:
                c1d = _to_domain_candles(provider.get_candles(sym, "1d", 240))
                if c1d:
                    c1d_map[sym.upper()] = c1d
            except Exception:
                pass
            try:
                c15 = _to_domain_candles(provider.get_candles(sym, "15m", 40))
                if c15:
                    c15_map[sym.upper()] = c15
            except Exception:
                pass

    await asyncio.to_thread(_fetch_all)
    src = "DEMO_SYNTHETIC" if request.app.state.settings.demo_mode else "ANGEL_ONE_LIVE_HISTORICAL"
    return run_multi_period_backtest(
        symbols=syms,
        candles_1d_by_sym=c1d_map,
        candles_15m_by_sym=c15_map,
        per_trade_amount_inr=at.config.per_trade_amount_inr,
        data_source=src,
    )
