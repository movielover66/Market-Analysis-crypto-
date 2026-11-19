"""
Milestone 3.3 — Deterministic Decimal Risk Engine (Section 14).
Sole authority for position quantity, risk amount, charges, slippage, exposure,
net R:R, daily/weekly loss lock, and paper-trade eligibility.
Gemini AI can NEVER override or modify these outputs.
"""
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from typing import Dict, List, Optional


def round_to_tick(price: Decimal, tick_size: Decimal = Decimal("0.05")) -> Decimal:
    if tick_size <= Decimal("0"):
        return price.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    steps = (price / tick_size).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return (steps * tick_size).quantize(Decimal("0.05"))


@dataclass(frozen=True)
class CostProfile:
    """
    Effective-dated configurable NSE charge profile (Section 14).
    Defaults represent typical NSE Cash Intraday / Delivery statutory + brokerage estimates.
    """
    mode: str = "SWING"  # "INTRADAY" | "SWING"
    brokerage_pct: Decimal = Decimal("0.03")
    brokerage_flat_max_inr: Decimal = Decimal("20.00")
    stt_buy_pct: Decimal = Decimal("0.10")     # 0.1% on delivery buy, 0% on intraday buy
    stt_sell_pct: Decimal = Decimal("0.10")    # 0.1% on delivery sell, 0.025% on intraday sell
    exchange_txn_pct: Decimal = Decimal("0.00325")
    sebi_Charges_pct: Decimal = Decimal("0.0001")
    stamp_duty_buy_pct: Decimal = Decimal("0.015")
    gst_pct: Decimal = Decimal("18.0")
    slippage_bps: Decimal = Decimal("10.0")    # 10 bps = 0.10% per leg

    @classmethod
    def for_mode(cls, mode: str, slippage_bps: Decimal = Decimal("10.0")) -> "CostProfile":
        if mode.upper() == "INTRADAY":
            return cls(
                mode="INTRADAY",
                brokerage_pct=Decimal("0.03"),
                brokerage_flat_max_inr=Decimal("20.00"),
                stt_buy_pct=Decimal("0.00"),
                stt_sell_pct=Decimal("0.025"),
                exchange_txn_pct=Decimal("0.00325"),
                sebi_Charges_pct=Decimal("0.0001"),
                stamp_duty_buy_pct=Decimal("0.003"),
                gst_pct=Decimal("18.0"),
                slippage_bps=slippage_bps,
            )
        return cls(mode="SWING", slippage_bps=slippage_bps)


@dataclass(frozen=True)
class RiskParameters:
    capital_inr: Decimal = Decimal("75000.00")
    risk_per_trade_pct: Decimal = Decimal("0.50")
    daily_loss_limit_pct: Decimal = Decimal("1.00")
    weekly_loss_limit_pct: Decimal = Decimal("2.50")
    max_open_positions: int = 2
    max_single_position_exposure_pct: Decimal = Decimal("25.00")
    minimum_net_rr: Decimal = Decimal("2.00")


@dataclass(frozen=True)
class AccountRiskState:
    current_open_positions: int = 0
    realised_daily_pnl_inr: Decimal = Decimal("0.00")
    realised_weekly_pnl_inr: Decimal = Decimal("0.00")


@dataclass(frozen=True)
class RiskEvaluationInput:
    symbol: str
    direction: str  # "LONG" | "SHORT"
    trading_mode: str  # "INTRADAY" | "SWING"
    entry_price: Decimal
    stop_loss: Decimal
    target_price: Decimal
    tick_size: Decimal = Decimal("0.05")
    freshness: str = "SIMULATED"
    instrument_status: str = "ACTIVE"
    strategy_confirmed: bool = True
    mtf_aligned: bool = True


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    rejection_code: Optional[str]
    rejection_reason: Optional[str]
    entry_price: Decimal
    stop_loss: Decimal
    target_price: Decimal
    quantity: int
    capital_used_inr: Decimal
    exposure_pct: Decimal
    allowed_risk_inr: Decimal
    all_in_risk_inr: Decimal
    estimated_charges_target_inr: Decimal
    estimated_slippage_inr: Decimal
    gross_reward_inr: Decimal
    net_reward_inr: Decimal
    gross_rr: Decimal
    net_rr: Decimal
    daily_lock_active: bool
    checks: Dict[str, bool] = field(default_factory=dict)


def compute_leg_charges(turnover_inr: Decimal, is_buy: bool, profile: CostProfile) -> Decimal:
    if turnover_inr <= Decimal("0"):
        return Decimal("0.00")
    brok_raw = turnover_inr * (profile.brokerage_pct / Decimal("100"))
    brok = min(brok_raw, profile.brokerage_flat_max_inr)
    stt_pct = profile.stt_buy_pct if is_buy else profile.stt_sell_pct
    stt = turnover_inr * (stt_pct / Decimal("100"))
    exch = turnover_inr * (profile.exchange_txn_pct / Decimal("100"))
    sebi = turnover_inr * (profile.sebi_Charges_pct / Decimal("100"))
    stamp = turnover_inr * (profile.stamp_duty_buy_pct / Decimal("100")) if is_buy else Decimal("0")
    gst = (brok + exch + sebi) * (profile.gst_pct / Decimal("100"))
    return (brok + stt + exch + sebi + stamp + gst).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def evaluate_trade_risk(
    inp: RiskEvaluationInput,
    params: RiskParameters,
    state: AccountRiskState,
    cost_profile: Optional[CostProfile] = None,
) -> RiskDecision:
    profile = cost_profile or CostProfile.for_mode(inp.trading_mode)
    entry = round_to_tick(inp.entry_price, inp.tick_size)
    stop = round_to_tick(inp.stop_loss, inp.tick_size)
    target = round_to_tick(inp.target_price, inp.tick_size)

    daily_limit_inr = (params.capital_inr * params.daily_loss_limit_pct / Decimal("100")).quantize(Decimal("0.01"))
    weekly_limit_inr = (params.capital_inr * params.weekly_loss_limit_pct / Decimal("100")).quantize(Decimal("0.01"))
    daily_locked = state.realised_daily_pnl_inr <= -daily_limit_inr
    weekly_locked = state.realised_weekly_pnl_inr <= -weekly_limit_inr
    allowed_risk_inr = (params.capital_inr * params.risk_per_trade_pct / Decimal("100")).quantize(Decimal("0.01"))

    def _reject(code: str, reason: str, checks: Dict[str, bool]) -> RiskDecision:
        return RiskDecision(
            approved=False,
            rejection_code=code,
            rejection_reason=reason,
            entry_price=entry,
            stop_loss=stop,
            target_price=target,
            quantity=0,
            capital_used_inr=Decimal("0.00"),
            exposure_pct=Decimal("0.00"),
            allowed_risk_inr=allowed_risk_inr,
            all_in_risk_inr=Decimal("0.00"),
            estimated_charges_target_inr=Decimal("0.00"),
            estimated_slippage_inr=Decimal("0.00"),
            gross_reward_inr=Decimal("0.00"),
            net_reward_inr=Decimal("0.00"),
            gross_rr=Decimal("0.00"),
            net_rr=Decimal("0.00"),
            daily_lock_active=daily_locked,
            checks=checks,
        )

    checks: Dict[str, bool] = {}

    # 1. Data freshness & instrument availability
    checks["data_fresh"] = inp.freshness not in ("STALE", "UNAVAILABLE")
    if not checks["data_fresh"]:
        return _reject("STALE_OR_UNAVAILABLE_DATA", f"Data freshness is {inp.freshness}; actionable plans require fresh data.", checks)

    checks["instrument_active"] = inp.instrument_status == "ACTIVE"
    if not checks["instrument_active"]:
        return _reject("INSTRUMENT_UNAVAILABLE", f"Instrument {inp.symbol} status is {inp.instrument_status}.", checks)

    # 2. Short constraint in SWING mode (NSE cash equities do not allow overnight short delivery without SLB)
    if inp.direction == "SHORT" and inp.trading_mode == "SWING":
        checks["mode_direction_valid"] = False
        return _reject("SHORT_SWING_NOT_PERMITTED", "Short positions in NSE cash equities are restricted to INTRADAY mode.", checks)
    checks["mode_direction_valid"] = True

    # 3. Daily / Weekly loss lock & open position limits
    checks["daily_lock_clear"] = not daily_locked
    if daily_locked:
        return _reject("DAILY_LOSS_LOCK_ACTIVE", f"Daily loss lock (-₹{daily_limit_inr}) is active.", checks)

    checks["weekly_lock_clear"] = not weekly_locked
    if weekly_locked:
        return _reject("WEEKLY_LOSS_LOCK_ACTIVE", f"Weekly loss lock (-₹{weekly_limit_inr}) is active.", checks)

    checks["open_positions_within_limit"] = state.current_open_positions < params.max_open_positions
    if not checks["open_positions_within_limit"]:
        return _reject("MAX_OPEN_POSITIONS_REACHED", f"Maximum open positions ({params.max_open_positions}) reached.", checks)

    # 4. Stop and Target geometry
    if entry <= 0 or stop <= 0 or target <= 0:
        return _reject("ZERO_OR_NEGATIVE_PRICE", "Entry, stop, and target prices must be positive.", checks)

    if inp.direction == "LONG":
        valid_sides = stop < entry < target
        raw_risk_ps = entry - stop
        raw_reward_ps = target - entry
    else:
        valid_sides = target < entry < stop
        raw_risk_ps = stop - entry
        raw_reward_ps = entry - target

    checks["valid_stop_target_sides"] = valid_sides
    if not valid_sides or raw_risk_ps <= Decimal("0"):
        return _reject("STOP_WRONG_SIDE", f"Invalid {inp.direction} price levels: entry={entry}, stop={stop}, target={target}.", checks)

    # 5. Strategy & MTF confirmation
    checks["strategy_confirmed"] = inp.strategy_confirmed
    if not inp.strategy_confirmed:
        return _reject("STRATEGY_NOT_CONFIRMED", "Strategy rules are not confirmed on closed candles.", checks)

    checks["mtf_aligned"] = inp.mtf_aligned
    if not inp.mtf_aligned:
        return _reject("MTF_CONFLICT", "Higher timeframe direction conflicts with setup.", checks)

    # 6. Cost-aware all-in loss per share & position sizing
    slip_rate = profile.slippage_bps / Decimal("10000")
    slip_per_share_stop = (entry + stop) * slip_rate
    est_var_cost_rate = (
        profile.brokerage_pct * Decimal("2")
        + profile.stt_buy_pct
        + profile.stt_sell_pct
        + profile.exchange_txn_pct * Decimal("2")
        + profile.stamp_duty_buy_pct
    ) / Decimal("100")
    approx_cost_ps = (entry + stop) * est_var_cost_rate
    all_in_loss_ps = raw_risk_ps + slip_per_share_stop + approx_cost_ps

    qty_by_risk = int((allowed_risk_inr / all_in_loss_ps).to_integral_value(rounding=ROUND_DOWN))
    max_exposure_inr = (params.capital_inr * params.max_single_position_exposure_pct / Decimal("100")).quantize(Decimal("0.01"))
    qty_by_exposure = int((max_exposure_inr / entry).to_integral_value(rounding=ROUND_DOWN))
    qty = min(qty_by_risk, qty_by_exposure)

    # Refine downward if flat brokerage ceiling pushes total stop loss above allowed_risk_inr
    while qty > 0:
        buy_turn = entry * Decimal(str(qty))
        stop_turn = stop * Decimal(str(qty))
        charges_stop = (
            compute_leg_charges(buy_turn, True, profile) + compute_leg_charges(stop_turn, False, profile)
            if inp.direction == "LONG"
            else compute_leg_charges(buy_turn, False, profile) + compute_leg_charges(stop_turn, True, profile)
        )
        slip_stop = ((entry + stop) * slip_rate * Decimal(str(qty))).quantize(Decimal("0.01"))
        total_stop_loss = (raw_risk_ps * Decimal(str(qty))) + charges_stop + slip_stop
        if total_stop_loss <= allowed_risk_inr:
            break
        qty -= 1

    checks["positive_quantity"] = qty >= 1
    if qty < 1:
        return _reject(
            "ZERO_QUANTITY_WITHIN_RISK",
            f"Allowed risk ₹{allowed_risk_inr} or exposure cap ₹{max_exposure_inr} cannot fit 1 share at ₹{entry} (stop ₹{stop}).",
            checks,
        )

    qty_dec = Decimal(str(qty))
    cap_used = (entry * qty_dec).quantize(Decimal("0.01"))
    exposure_pct = ((cap_used / params.capital_inr) * Decimal("100")).quantize(Decimal("0.01"))

    buy_turn = entry * qty_dec
    stop_turn = stop * qty_dec
    target_turn = target * qty_dec

    if inp.direction == "LONG":
        charges_stop = compute_leg_charges(buy_turn, True, profile) + compute_leg_charges(stop_turn, False, profile)
        charges_target = compute_leg_charges(buy_turn, True, profile) + compute_leg_charges(target_turn, False, profile)
    else:
        charges_stop = compute_leg_charges(buy_turn, False, profile) + compute_leg_charges(stop_turn, True, profile)
        charges_target = compute_leg_charges(buy_turn, False, profile) + compute_leg_charges(target_turn, True, profile)

    slip_stop_inr = ((entry + stop) * slip_rate * qty_dec).quantize(Decimal("0.01"))
    slip_target_inr = ((entry + target) * slip_rate * qty_dec).quantize(Decimal("0.01"))

    all_in_risk = ((raw_risk_ps * qty_dec) + charges_stop + slip_stop_inr).quantize(Decimal("0.01"))
    gross_reward = (raw_reward_ps * qty_dec).quantize(Decimal("0.01"))
    net_reward = (gross_reward - charges_target - slip_target_inr).quantize(Decimal("0.01"))

    gross_rr = (raw_reward_ps / raw_risk_ps).quantize(Decimal("0.01"))
    net_rr = (net_reward / all_in_risk).quantize(Decimal("0.01")) if all_in_risk > Decimal("0") else Decimal("0.00")

    checks["net_rr_meets_minimum"] = net_rr >= params.minimum_net_rr
    if not checks["net_rr_meets_minimum"]:
        return _reject(
            "NET_RR_BELOW_MINIMUM",
            f"Net R:R ({net_rr}) after charges (₹{charges_target}) and slippage (₹{slip_target_inr}) is below minimum {params.minimum_net_rr}.",
            checks,
        )

    return RiskDecision(
        approved=True,
        rejection_code=None,
        rejection_reason=None,
        entry_price=entry,
        stop_loss=stop,
        target_price=target,
        quantity=qty,
        capital_used_inr=cap_used,
        exposure_pct=exposure_pct,
        allowed_risk_inr=allowed_risk_inr,
        all_in_risk_inr=all_in_risk,
        estimated_charges_target_inr=charges_target,
        estimated_slippage_inr=slip_target_inr,
        gross_reward_inr=gross_reward,
        net_reward_inr=net_reward,
        gross_rr=gross_rr,
        net_rr=net_rr,
        daily_lock_active=False,
        checks=checks,
    )
