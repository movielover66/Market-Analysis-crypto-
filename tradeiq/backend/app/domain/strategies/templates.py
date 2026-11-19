"""
Milestone 3.4 — Four Initial Deterministic Strategy Templates (Section 12).
  - Strategy A: BREAKOUT_RETEST
  - Strategy B: SUPPORT_BOUNCE
  - Strategy C: TREND_FOLLOWING
  - Strategy D: BREAKDOWN_RETEST

Every strategy evaluates ONLY closed candles (`closed_candles_only`), records
rule-by-rule pass/fail evidence with observed values & thresholds, and never
allows an unconfirmed/near-zone price to masquerade as CONFIRMED.
"""
import datetime
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional

from ..candles.validator import DomainCandle
from ..indicators.core import (
    closed_candles_only,
    ema_series,
    volume_metrics,
    wilder_atr_series,
    wilder_rsi_series,
)
from ..risk.risk_engine import round_to_tick
from ..structure.market_structure import (
    SupportResistanceZone,
    build_sr_zones,
    classify_trend_structure,
)


@dataclass(frozen=True)
class RuleCheckResult:
    rule_id: str
    description: str
    passed: bool
    observed_value: str
    threshold_value: str


@dataclass(frozen=True)
class StrategyResult:
    strategy_code: str
    strategy_version: str
    symbol: str
    timeframe: str
    as_of_ist: str
    status: str  # "CONFIRMED" | "WAITING" | "CANDIDATE" | "NO_TRADE" | "UNAVAILABLE"
    direction: str  # "LONG" | "SHORT"
    proposed_entry: Optional[Decimal]
    proposed_stop: Optional[Decimal]
    proposed_target: Optional[Decimal]
    invalidation_condition: str
    is_provisional: bool
    rule_results: List[RuleCheckResult] = field(default_factory=list)
    explanation_keys: List[str] = field(default_factory=list)


class BaseStrategy:
    code: str = "BASE"
    version: str = "1.0.0"

    def evaluate(
        self,
        candles_by_timeframe: Dict[str, List[DomainCandle]],
        market_context: Dict[str, Any],
        params: Optional[Dict[str, Any]] = None,
        as_of: Optional[datetime.datetime] = None,
    ) -> StrategyResult:
        raise NotImplementedError


class BreakoutRetestStrategy(BaseStrategy):
    """Strategy A — Breakout + Retest (LONG)."""
    code = "BREAKOUT_RETEST"
    version = "1.0.0"

    def evaluate(
        self,
        candles_by_timeframe: Dict[str, List[DomainCandle]],
        market_context: Dict[str, Any],
        params: Optional[Dict[str, Any]] = None,
        as_of: Optional[datetime.datetime] = None,
    ) -> StrategyResult:
        cfg = params or {}
        tf = cfg.get("setup_timeframe", "15m")
        raw = candles_by_timeframe.get(tf) or []
        closed = closed_candles_only(raw)
        has_provisional = any(not c.is_closed for c in raw)
        sym = raw[0].symbol if raw else market_context.get("symbol", "UNKNOWN")
        as_of_str = closed[-1].ts_ist.isoformat() if closed else (as_of.isoformat() if as_of else "")

        if len(closed) < 25:
            return StrategyResult(
                strategy_code=self.code, strategy_version=self.version, symbol=sym, timeframe=tf,
                as_of_ist=as_of_str, status="UNAVAILABLE", direction="LONG",
                proposed_entry=None, proposed_stop=None, proposed_target=None,
                invalidation_condition="Insufficient closed bars (< 25)", is_provisional=has_provisional,
                rule_results=[RuleCheckResult("MIN_BARS", "At least 25 closed bars required", False, str(len(closed)), "25")],
                explanation_keys=["INSUFFICIENT_DATA"],
            )

        min_rvol = Decimal(str(cfg.get("min_rvol", "1.25")))
        retest_tol_atr = Decimal(str(cfg.get("retest_tolerance_atr", "0.60")))

        # Build resistance zones from bars prior to the last 3 bars so breakout/retest can be detected cleanly
        prior_bars = closed[:-3]
        zones = [z for z in build_sr_zones(prior_bars, k=2) if z.kind == "RESISTANCE"]
        if not zones:
            prior_high = max(c.high for c in prior_bars[-15:])
            atrs_p = wilder_atr_series(prior_bars, 14)
            atr_p = atrs_p[-1] or (prior_high * Decimal("0.01"))
            hw = (atr_p * Decimal("0.25")).quantize(Decimal("0.05"))
            zones = [SupportResistanceZone("RESISTANCE", prior_high, prior_high - hw, prior_high + hw, 2, 70)]

        res_zone = zones[-1]
        atrs = wilder_atr_series(closed, 14)
        atr_val = atrs[-1] or (closed[-1].close * Decimal("0.01"))
        v_met = volume_metrics(closed, 20)
        rvol_val = v_met["rvol"] or Decimal("1.00")

        # Check recent 3 closed bars for breakout above resistance + retest hold
        recent = closed[-3:]
        broke_out = any(b.close > res_zone.upper for b in recent)
        last_bar = closed[-1]
        retest_band_top = res_zone.upper + (atr_val * retest_tol_atr)
        retest_touched = last_bar.low <= retest_band_top
        retest_held = last_bar.close > res_zone.upper and last_bar.close >= last_bar.open
        vol_ok = rvol_val >= min_rvol

        rules = [
            RuleCheckResult("R1_RESISTANCE_ZONE", "Resistance zone identified", True, f"{res_zone.lower}–{res_zone.upper}", "Valid zone"),
            RuleCheckResult("R2_CLOSED_BREAKOUT", "Closed candle broke above resistance", broke_out, str(last_bar.close), f"> {res_zone.upper}"),
            RuleCheckResult("R3_VOLUME_CONFIRM", "Relative volume meets threshold", vol_ok, str(rvol_val), f">= {min_rvol}"),
            RuleCheckResult("R4_RETEST_HOLD", "Retest touched zone and held on closed candle", bool(broke_out and retest_touched and retest_held), f"low={last_bar.low}, close={last_bar.close}", f"low <= {retest_band_top:.2f} & close > {res_zone.upper}"),
        ]

        entry = round_to_tick(last_bar.close)
        stop = round_to_tick(res_zone.lower - (atr_val * Decimal("0.50")))
        risk = max(Decimal("0.05"), entry - stop)
        target = round_to_tick(entry + (risk * Decimal("2.50")))

        if all(r.passed for r in rules):
            status = "CONFIRMED"
            keys = ["BREAKOUT_CONFIRMED", "RETEST_HELD", "VOLUME_SUPPORTED"]
        elif broke_out and not (retest_touched and retest_held):
            status = "WAITING"
            keys = ["BREAKOUT_SEEN_WAITING_FOR_RETEST"]
        elif last_bar.close >= res_zone.lower * Decimal("0.99"):
            status = "CANDIDATE"
            keys = ["NEAR_RESISTANCE_NOT_BROKEN"]
        else:
            status = "NO_TRADE"
            keys = ["NO_BREAKOUT_SETUP"]

        return StrategyResult(
            strategy_code=self.code, strategy_version=self.version, symbol=sym, timeframe=tf,
            as_of_ist=as_of_str, status=status, direction="LONG",
            proposed_entry=entry, proposed_stop=stop, proposed_target=target,
            invalidation_condition=f"Closed {tf} candle below ₹{stop}",
            is_provisional=has_provisional, rule_results=rules, explanation_keys=keys,
        )


class SupportBounceStrategy(BaseStrategy):
    """Strategy B — Support Bounce (LONG)."""
    code = "SUPPORT_BOUNCE"
    version = "1.0.0"

    def evaluate(
        self,
        candles_by_timeframe: Dict[str, List[DomainCandle]],
        market_context: Dict[str, Any],
        params: Optional[Dict[str, Any]] = None,
        as_of: Optional[datetime.datetime] = None,
    ) -> StrategyResult:
        cfg = params or {}
        tf = cfg.get("setup_timeframe", "1h")
        raw = candles_by_timeframe.get(tf) or []
        closed = closed_candles_only(raw)
        has_provisional = any(not c.is_closed for c in raw)
        sym = raw[0].symbol if raw else market_context.get("symbol", "UNKNOWN")
        as_of_str = closed[-1].ts_ist.isoformat() if closed else ""

        if len(closed) < 25:
            return StrategyResult(
                self.code, self.version, sym, tf, as_of_str, "UNAVAILABLE", "LONG",
                None, None, None, "Insufficient closed bars (< 25)", has_provisional,
                [RuleCheckResult("MIN_BARS", "At least 25 closed bars", False, str(len(closed)), "25")],
                ["INSUFFICIENT_DATA"],
            )

        prior_bars = closed[:-2]
        sup_zones = [z for z in build_sr_zones(prior_bars, k=2) if z.kind == "SUPPORT"]
        atrs = wilder_atr_series(closed, 14)
        atr_val = atrs[-1] or (closed[-1].close * Decimal("0.01"))
        if not sup_zones:
            lo = min(c.low for c in prior_bars[-15:])
            hw = (atr_val * Decimal("0.25")).quantize(Decimal("0.05"))
            sup_zones = [SupportResistanceZone("SUPPORT", lo, lo - hw, lo + hw, 2, 70)]

        sup = sup_zones[0]
        last = closed[-1]
        closes = [c.close for c in closed]
        rsi_val = wilder_rsi_series(closes, 14)[-1] or Decimal("50.0")

        tested_zone = last.low <= sup.upper + (atr_val * Decimal("0.30")) and last.close > sup.lower
        bullish_rejection = last.close > last.open and last.close > sup.upper
        momentum_ok = Decimal("35.0") <= rsi_val <= Decimal("68.0")

        rules = [
            RuleCheckResult("R1_SUPPORT_ZONE", "Support zone identified", True, f"{sup.lower}–{sup.upper}", "Valid support"),
            RuleCheckResult("R2_ZONE_TESTED", "Price tested support zone", tested_zone, f"low={last.low}", f"<= {sup.upper + atr_val * Decimal('0.30'):.2f}"),
            RuleCheckResult("R3_CLOSED_REJECTION", "Bullish rejection candle closed above support", bullish_rejection, f"open={last.open}, close={last.close}", f"close > open & > {sup.upper}"),
            RuleCheckResult("R4_MOMENTUM_OK", "RSI(14) in healthy bounce range (35–68)", momentum_ok, str(rsi_val), "35.0–68.0"),
        ]

        entry = round_to_tick(last.close)
        stop = round_to_tick(min(last.low, sup.lower) - (atr_val * Decimal("0.50")))
        risk = max(Decimal("0.05"), entry - stop)
        target = round_to_tick(entry + (risk * Decimal("2.50")))

        status = "CONFIRMED" if all(r.passed for r in rules) else ("WAITING" if tested_zone else "NO_TRADE")
        return StrategyResult(
            self.code, self.version, sym, tf, as_of_str, status, "LONG",
            entry, stop, target, f"Closed {tf} candle below ₹{stop}",
            has_provisional, rules, ["SUPPORT_BOUNCE_CONFIRMED" if status == "CONFIRMED" else "SUPPORT_NOT_CONFIRMED"],
        )


class TrendFollowingStrategy(BaseStrategy):
    """Strategy C — Trend Following Pullback Continuation (LONG)."""
    code = "TREND_FOLLOWING"
    version = "1.0.0"

    def evaluate(
        self,
        candles_by_timeframe: Dict[str, List[DomainCandle]],
        market_context: Dict[str, Any],
        params: Optional[Dict[str, Any]] = None,
        as_of: Optional[datetime.datetime] = None,
    ) -> StrategyResult:
        cfg = params or {}
        tf = cfg.get("setup_timeframe", "1h")
        raw = candles_by_timeframe.get(tf) or []
        closed = closed_candles_only(raw)
        has_provisional = any(not c.is_closed for c in raw)
        sym = raw[0].symbol if raw else market_context.get("symbol", "UNKNOWN")
        as_of_str = closed[-1].ts_ist.isoformat() if closed else ""

        if len(closed) < 50:
            return StrategyResult(
                self.code, self.version, sym, tf, as_of_str, "UNAVAILABLE", "LONG",
                None, None, None, "Requires >= 50 closed bars for EMA 50", has_provisional,
                [RuleCheckResult("MIN_BARS", "At least 50 closed bars", False, str(len(closed)), "50")],
                ["INSUFFICIENT_DATA"],
            )

        closes = [c.close for c in closed]
        e9 = ema_series(closes, 9)[-1] or closes[-1]
        e20 = ema_series(closes, 20)[-1] or closes[-1]
        e50 = ema_series(closes, 50)[-1] or closes[-1]
        atrs = wilder_atr_series(closed, 14)
        atr_val = atrs[-1] or (closes[-1] * Decimal("0.01"))
        last = closed[-1]
        trend = classify_trend_structure(closed, k=2)

        ema_stacked = e9 >= e20 > e50
        structure_ok = trend == "UPTREND_HH_HL" or (last.close > e20 and e20 > e50)
        not_overextended = (last.close - e20) <= (atr_val * Decimal("2.20"))
        trigger_ok = last.close > last.open and last.close > e9

        rules = [
            RuleCheckResult("R1_TREND_STRUCTURE", "Uptrend structure & price > EMA20 > EMA50", structure_ok, trend, "UPTREND"),
            RuleCheckResult("R2_EMA_ALIGNMENT", "EMA 9 >= EMA 20 > EMA 50", ema_stacked, f"{e9:.2f} / {e20:.2f} / {e50:.2f}", "e9 >= e20 > e50"),
            RuleCheckResult("R3_NOT_EXTENDED", "Price within 2.2x ATR of EMA 20", not_overextended, f"dist={(last.close - e20):.2f}", f"<= {(atr_val * Decimal('2.20')):.2f}"),
            RuleCheckResult("R4_CONTINUATION_TRIGGER", "Closed bullish candle above EMA 9", trigger_ok, f"close={last.close}", f"> {e9:.2f} & green"),
        ]

        entry = round_to_tick(last.close)
        stop = round_to_tick(min(last.low, e20) - (atr_val * Decimal("0.80")))
        risk = max(Decimal("0.05"), entry - stop)
        target = round_to_tick(entry + (risk * Decimal("2.50")))

        status = "CONFIRMED" if all(r.passed for r in rules) else ("WAITING" if (structure_ok and ema_stacked) else "NO_TRADE")
        return StrategyResult(
            self.code, self.version, sym, tf, as_of_str, status, "LONG",
            entry, stop, target, f"Closed {tf} candle below EMA20 / ₹{stop}",
            has_provisional, rules, ["TREND_FOLLOWING_CONFIRMED" if status == "CONFIRMED" else "TREND_WAITING"],
        )


class BreakdownRetestStrategy(BaseStrategy):
    """Strategy D — Breakdown + Retest (SHORT — Intraday Only in NSE Cash)."""
    code = "BREAKDOWN_RETEST"
    version = "1.0.0"

    def evaluate(
        self,
        candles_by_timeframe: Dict[str, List[DomainCandle]],
        market_context: Dict[str, Any],
        params: Optional[Dict[str, Any]] = None,
        as_of: Optional[datetime.datetime] = None,
    ) -> StrategyResult:
        cfg = params or {}
        tf = cfg.get("setup_timeframe", "15m")
        raw = candles_by_timeframe.get(tf) or []
        closed = closed_candles_only(raw)
        has_provisional = any(not c.is_closed for c in raw)
        sym = raw[0].symbol if raw else market_context.get("symbol", "UNKNOWN")
        as_of_str = closed[-1].ts_ist.isoformat() if closed else ""

        if len(closed) < 25:
            return StrategyResult(
                self.code, self.version, sym, tf, as_of_str, "UNAVAILABLE", "SHORT",
                None, None, None, "Insufficient closed bars (< 25)", has_provisional,
                [RuleCheckResult("MIN_BARS", "At least 25 closed bars", False, str(len(closed)), "25")],
                ["INSUFFICIENT_DATA"],
            )

        prior_bars = closed[:-3]
        atrs = wilder_atr_series(closed, 14)
        atr_val = atrs[-1] or (closed[-1].close * Decimal("0.01"))
        sup_zones = [z for z in build_sr_zones(prior_bars, k=2) if z.kind == "SUPPORT"]
        if not sup_zones:
            lo = min(c.low for c in prior_bars[-15:])
            hw = (atr_val * Decimal("0.25")).quantize(Decimal("0.05"))
            sup_zones = [SupportResistanceZone("SUPPORT", lo, lo - hw, lo + hw, 2, 70)]

        sup = sup_zones[0]
        recent = closed[-3:]
        broke_down = any(b.close < sup.lower for b in recent)
        last = closed[-1]
        retest_band_low = sup.lower - (atr_val * Decimal("0.60"))
        retest_touched = last.high >= retest_band_low
        retest_failed = last.close < sup.lower and last.close <= last.open
        rvol_val = (volume_metrics(closed, 20)["rvol"]) or Decimal("1.00")
        vol_ok = rvol_val >= Decimal("1.20")

        rules = [
            RuleCheckResult("R1_SUPPORT_ZONE", "Support zone identified", True, f"{sup.lower}–{sup.upper}", "Valid support"),
            RuleCheckResult("R2_CLOSED_BREAKDOWN", "Closed candle broke below support", broke_down, str(last.close), f"< {sup.lower}"),
            RuleCheckResult("R3_VOLUME_CONFIRM", "Breakdown volume RVOL >= 1.20", vol_ok, str(rvol_val), ">= 1.20"),
            RuleCheckResult("R4_RETEST_FAILED", "Retest failed below broken support on closed candle", bool(broke_down and retest_touched and retest_failed), f"high={last.high}, close={last.close}", f"close < {sup.lower} & red"),
        ]

        entry = round_to_tick(last.close)
        stop = round_to_tick(sup.upper + (atr_val * Decimal("0.50")))
        risk = max(Decimal("0.05"), stop - entry)
        target = round_to_tick(max(Decimal("0.05"), entry - (risk * Decimal("2.50"))))

        status = "CONFIRMED" if all(r.passed for r in rules) else ("WAITING" if broke_down else "NO_TRADE")
        return StrategyResult(
            self.code, self.version, sym, tf, as_of_str, status, "SHORT",
            entry, stop, target, f"Closed {tf} candle above ₹{stop}",
            has_provisional, rules, ["BREAKDOWN_CONFIRMED" if status == "CONFIRMED" else "NO_BREAKDOWN"],
        )


STRATEGY_REGISTRY: Dict[str, BaseStrategy] = {
    "BREAKOUT_RETEST": BreakoutRetestStrategy(),
    "SUPPORT_BOUNCE": SupportBounceStrategy(),
    "TREND_FOLLOWING": TrendFollowingStrategy(),
    "BREAKDOWN_RETEST": BreakdownRetestStrategy(),
}
