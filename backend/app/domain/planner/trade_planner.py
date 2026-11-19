"""
Milestone 3.6 & 3.7 — Shared Scanner, Trade Planner & Status Machine.
Uses the exact same strategy, MTF aligner, and Decimal risk engine.
Handles plan expiry, invalidation, stale data, and append-only PlanEvents.
"""
import datetime
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional

from ..calendar.nse_calendar import to_ist
from ..candles.validator import DomainCandle
from ..risk.risk_engine import (
    AccountRiskState,
    RiskDecision,
    RiskEvaluationInput,
    RiskParameters,
    evaluate_trade_risk,
)
from ..strategies.mtf_aligner import MTFAlignmentResult, evaluate_mtf_alignment
from ..strategies.templates import STRATEGY_REGISTRY, StrategyResult


@dataclass(frozen=True)
class PlanEvent:
    from_status: Optional[str]
    to_status: str
    reason: str
    ts_ist: str


@dataclass(frozen=True)
class TradePlanProposal:
    plan_id: str
    symbol: str
    trading_mode: str
    strategy_code: str
    strategy_version: str
    status: str  # "CONFIRMED" | "WAITING" | "CANDIDATE" | "REJECTED" | "NO_TRADE" | "EXPIRED" | "INVALIDATED" | "UNAVAILABLE"
    direction: str
    freshness: str
    strategy_result: StrategyResult
    mtf_result: MTFAlignmentResult
    risk_decision: Optional[RiskDecision]
    expires_at_ist: str
    invalidation_condition: str
    disclaimer: str
    events: List[PlanEvent] = field(default_factory=list)


DISCLAIMER_TEXT = (
    "Analytical & educational paper-trading tool only. Not investment advice or a guarantee of results."
)


def build_trade_plan(
    symbol: str,
    strategy_code: str,
    trading_mode: str,
    candles_by_tf: Dict[str, List[DomainCandle]],
    risk_params: RiskParameters,
    account_state: AccountRiskState,
    freshness: str = "SIMULATED",
    instrument_status: str = "ACTIVE",
    tick_size: Decimal = Decimal("0.05"),
    now_dt: Optional[datetime.datetime] = None,
) -> TradePlanProposal:
    now_ist = to_ist(now_dt or datetime.datetime.now(datetime.timezone.utc))
    strat = STRATEGY_REGISTRY.get(strategy_code)
    if not strat:
        raise ValueError(f"Unknown strategy_code: {strategy_code!r}")

    setup_tf = "15m" if trading_mode == "INTRADAY" else "1h"
    trigger_tf = "5m" if trading_mode == "INTRADAY" else "15m"
    context_tf = "1d"

    strat_res = strat.evaluate(
        candles_by_tf,
        market_context={"symbol": symbol},
        params={"setup_timeframe": setup_tf},
        as_of=now_ist,
    )
    mtf_res = evaluate_mtf_alignment(
        candles_by_tf,
        setup_direction=strat_res.direction,
        context_tf=context_tf,
        setup_tf=setup_tf,
        trigger_tf=trigger_tf,
    )

    events: List[PlanEvent] = [
        PlanEvent(None, strat_res.status, f"Strategy {strategy_code} evaluated as {strat_res.status}", now_ist.isoformat())
    ]

    expires_at = (
        now_ist.replace(hour=15, minute=25, second=0, microsecond=0)
        if trading_mode == "INTRADAY"
        else now_ist + datetime.timedelta(days=3)
    )

    if strat_res.proposed_entry is None or strat_res.proposed_stop is None or strat_res.proposed_target is None:
        return TradePlanProposal(
            plan_id=str(uuid.uuid4()),
            symbol=symbol,
            trading_mode=trading_mode,
            strategy_code=strategy_code,
            strategy_version=strat.version,
            status="UNAVAILABLE",
            direction=strat_res.direction,
            freshness=freshness,
            strategy_result=strat_res,
            mtf_result=mtf_res,
            risk_decision=None,
            expires_at_ist=expires_at.isoformat(),
            invalidation_condition=strat_res.invalidation_condition,
            disclaimer=DISCLAIMER_TEXT,
            events=events,
        )

    risk_inp = RiskEvaluationInput(
        symbol=symbol,
        direction=strat_res.direction,
        trading_mode=trading_mode,
        entry_price=strat_res.proposed_entry,
        stop_loss=strat_res.proposed_stop,
        target_price=strat_res.proposed_target,
        tick_size=tick_size,
        freshness=freshness,
        instrument_status=instrument_status,
        strategy_confirmed=(strat_res.status == "CONFIRMED"),
        mtf_aligned=(mtf_res.final_status == "ALIGNED"),
    )
    risk_dec = evaluate_trade_risk(risk_inp, risk_params, account_state)

    if risk_dec.approved:
        final_status = "CONFIRMED"
        events.append(PlanEvent(strat_res.status, "CONFIRMED", f"Risk approved ({risk_dec.quantity} shares, net R:R {risk_dec.net_rr})", now_ist.isoformat()))
    else:
        if strat_res.status in ("WAITING", "CANDIDATE") and risk_dec.rejection_code == "STRATEGY_NOT_CONFIRMED":
            final_status = strat_res.status
            events.append(PlanEvent(strat_res.status, final_status, "Setup not yet confirmed on closed candle", now_ist.isoformat()))
        else:
            final_status = "REJECTED"
            events.append(PlanEvent(strat_res.status, "REJECTED", f"{risk_dec.rejection_code}: {risk_dec.rejection_reason}", now_ist.isoformat()))

    return TradePlanProposal(
        plan_id=str(uuid.uuid4()),
        symbol=symbol,
        trading_mode=trading_mode,
        strategy_code=strategy_code,
        strategy_version=strat.version,
        status=final_status,
        direction=strat_res.direction,
        freshness=freshness,
        strategy_result=strat_res,
        mtf_result=mtf_res,
        risk_decision=risk_dec,
        expires_at_ist=expires_at.isoformat(),
        invalidation_condition=strat_res.invalidation_condition,
        disclaimer=DISCLAIMER_TEXT,
        events=events,
    )


def refresh_plan_lifecycle(
    plan: TradePlanProposal,
    latest_price: Decimal,
    latest_freshness: str,
    now_dt: datetime.datetime,
) -> TradePlanProposal:
    """
    Transitions active plans to EXPIRED, INVALIDATED, or REJECTED if stale/out-of-zone.
    """
    now_ist = to_ist(now_dt)
    exp_dt = datetime.datetime.fromisoformat(plan.expires_at_ist)
    events = list(plan.events)

    if latest_freshness in ("STALE", "UNAVAILABLE"):
        events.append(PlanEvent(plan.status, "REJECTED", f"Data became {latest_freshness}", now_ist.isoformat()))
        return TradePlanProposal(**{**plan.__dict__, "status": "REJECTED", "freshness": latest_freshness, "events": events})

    if now_ist >= exp_dt and plan.status in ("CONFIRMED", "WAITING", "CANDIDATE"):
        events.append(PlanEvent(plan.status, "EXPIRED", "Plan expiry timestamp reached", now_ist.isoformat()))
        return TradePlanProposal(**{**plan.__dict__, "status": "EXPIRED", "events": events})

    if plan.risk_decision:
        sl = plan.risk_decision.stop_loss
        if (plan.direction == "LONG" and latest_price <= sl) or (plan.direction == "SHORT" and latest_price >= sl):
            events.append(PlanEvent(plan.status, "INVALIDATED", f"Price ₹{latest_price} breached stop ₹{sl}", now_ist.isoformat()))
            return TradePlanProposal(**{**plan.__dict__, "status": "INVALIDATED", "events": events})

    return plan
