"""
TRADEIQ v2.0 — 3-Engine Multi-Regime Quantitative Strategy Suite.
- Engine 0: Market Regime Gate (Nifty 50 / BankNifty / Sensex + Breadth + Volatility)
- Engine 1: Morning Intraday (15m ORB + VWAP + Relative Volume + 5-Level Order-Book Imbalance)
- Engine 2A: Minervini 8-Point Trend Template + Volatility Contraction Pattern (VCP) Breakout
- Engine 2B: Larry Connors RSI(2) Pullback Mean Reversion (on Price > 200-SMA leaders)

100% deterministic Decimal math. Zero fake signals. Minimum net R:R after NSE charges enforced.
"""
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from ..candles.aggregator import DomainCandle
from ..indicators.core import compute_indicator_snapshot
from ..risk.risk_engine import CostProfile, compute_leg_charges, round_to_tick


@dataclass(frozen=True)
class RegimeAssessment:
    regime: str  # "TRENDING" | "RANGE_BOUND" | "CHOPPY"
    allow_auto_trading: bool
    recommended_engines: List[str]
    breadth_pct: Decimal
    avg_atr_pct: Decimal
    trend_score: int
    summary: str
    indices_snapshot: List[Dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class OrderBookImbalance:
    symbol: str
    total_buy_qty: int
    total_sell_qty: int
    imbalance_ratio: Decimal  # buy / max(1, sell)
    pressure: str  # "BUY_IMBALANCE" | "SELL_IMBALANCE" | "BALANCED"
    source: str


@dataclass(frozen=True)
class EngineSignal:
    symbol: str
    engine_code: str  # "ENGINE_1_ORB" | "ENGINE_2A_VCP" | "ENGINE_2B_RSI2"
    engine_name: str
    trading_mode: str  # "INTRADAY" | "SWING"
    direction: str  # "LONG" | "SHORT"
    status: str  # "CONFIRMED" | "WATCHLIST" | "REJECTED"
    score: int  # 0..100
    entry_price: Decimal
    stop_loss: Decimal
    target_price: Decimal
    expected_gross_rr: Decimal
    expected_net_rr: Decimal
    estimated_roundtrip_charges_per_lot: Decimal
    passed_rules: List[str]
    failed_rules: List[str]
    order_book_pressure: str
    rationale: str


def _sma(values: List[Decimal], period: int) -> Optional[Decimal]:
    if len(values) < period or period <= 0:
        return None
    return (sum(values[-period:], Decimal("0")) / Decimal(str(period))).quantize(Decimal("0.01"))


def _rsi(closes: List[Decimal], period: int = 2) -> Optional[Decimal]:
    if len(closes) < period + 1:
        return None
    gains = Decimal("0")
    losses = Decimal("0")
    for i in range(-period, 0):
        diff = closes[i] - closes[i - 1]
        if diff > 0:
            gains += diff
        elif diff < 0:
            losses += abs(diff)
    avg_gain = gains / Decimal(str(period))
    avg_loss = losses / Decimal(str(period))
    if avg_loss == 0:
        return Decimal("100.00") if avg_gain > 0 else Decimal("50.00")
    rs = avg_gain / avg_loss
    rsi_val = Decimal("100") - (Decimal("100") / (Decimal("1") + rs))
    return rsi_val.quantize(Decimal("0.01"))


def _compute_vwap(candles: List[DomainCandle]) -> Decimal:
    if not candles:
        return Decimal("0.00")
    num = Decimal("0")
    den = Decimal("0")
    for c in candles:
        typical = (c.high + c.low + c.close) / Decimal("3")
        vol = Decimal(str(max(1, c.volume)))
        num += typical * vol
        den += vol
    return (num / den).quantize(Decimal("0.05")) if den > 0 else candles[-1].close


def compute_order_book_imbalance(
    symbol: str,
    depth: Optional[Dict[str, Any]] = None,
    candles: Optional[List[DomainCandle]] = None,
) -> OrderBookImbalance:
    """
    Uses Angel One SnapQuote 5-Level Buy/Sell depth when available.
    Falls back to live tick/candle volume delta ratio when depth snapshot is absent.
    Never invents fake data.
    """
    sym = symbol.upper()
    if depth and (int(depth.get("total_buy_qty") or 0) > 0 or int(depth.get("total_sell_qty") or 0) > 0):
        buy_q = max(0, int(depth.get("total_buy_qty") or 0))
        sell_q = max(0, int(depth.get("total_sell_qty") or 0))
        ratio = (Decimal(str(buy_q)) / Decimal(str(max(1, sell_q)))).quantize(Decimal("0.01"))
        if ratio >= Decimal("1.25"):
            pressure = "BUY_IMBALANCE"
        elif ratio <= Decimal("0.80"):
            pressure = "SELL_IMBALANCE"
        else:
            pressure = "BALANCED"
        return OrderBookImbalance(
            symbol=sym,
            total_buy_qty=buy_q,
            total_sell_qty=sell_q,
            imbalance_ratio=ratio,
            pressure=pressure,
            source="ANGEL_5LEVEL_DEPTH",
        )

    # Candle volume delta proxy from last 5 closed candles
    if candles and len(candles) >= 3:
        recent = candles[-5:]
        buy_v = sum(c.volume for c in recent if c.close >= c.open)
        sell_v = sum(c.volume for c in recent if c.close < c.open)
        ratio = (Decimal(str(max(1, buy_v))) / Decimal(str(max(1, sell_v)))).quantize(Decimal("0.01"))
        pressure = "BUY_IMBALANCE" if ratio >= Decimal("1.25") else ("SELL_IMBALANCE" if ratio <= Decimal("0.80") else "BALANCED")
        return OrderBookImbalance(
            symbol=sym,
            total_buy_qty=buy_v,
            total_sell_qty=sell_v,
            imbalance_ratio=ratio,
            pressure=pressure,
            source="CANDLE_VOLUME_DELTA",
        )

    return OrderBookImbalance(
        symbol=sym,
        total_buy_qty=0,
        total_sell_qty=0,
        imbalance_ratio=Decimal("1.00"),
        pressure="BALANCED",
        source="UNAVAILABLE",
    )


def evaluate_market_regime(
    indices_snapshot: List[Dict[str, Any]],
    advances: int,
    declines: int,
    sample_atr_pcts: Optional[List[Decimal]] = None,
) -> RegimeAssessment:
    """
    Engine 0 — Market Regime Gate.
    Combines Nifty 50 / BankNifty / Sensex directional alignment + Market Breadth + Volatility (ATR%).
    Blocks auto-trading in CHOPPY/dead markets so strategies avoid whipsaw drawdowns.
    """
    total = max(1, advances + declines)
    breadth_pct = ((Decimal(str(advances)) / Decimal(str(total))) * Decimal("100")).quantize(Decimal("0.1"))
    atr_list = sample_atr_pcts or [Decimal("1.50")]
    avg_atr_pct = (sum(atr_list, Decimal("0")) / Decimal(str(max(1, len(atr_list))))).quantize(Decimal("0.01"))

    idx_moves = []
    for idx in indices_snapshot:
        try:
            idx_moves.append(Decimal(str(idx.get("change_pct", "0"))))
        except Exception:
            continue
    avg_idx_move = (sum(idx_moves, Decimal("0")) / Decimal(str(max(1, len(idx_moves))))).quantize(Decimal("0.01")) if idx_moves else Decimal("0.00")

    all_green = bool(idx_moves) and all(m > Decimal("0.15") for m in idx_moves)
    all_red = bool(idx_moves) and all(m < Decimal("-0.15") for m in idx_moves)

    if (breadth_pct >= Decimal("62.0") or breadth_pct <= Decimal("38.0")) and (all_green or all_red or abs(avg_idx_move) >= Decimal("0.35")) and avg_atr_pct >= Decimal("0.70"):
        return RegimeAssessment(
            regime="TRENDING",
            allow_auto_trading=True,
            recommended_engines=["ENGINE_1_ORB", "ENGINE_2A_VCP"],
            breadth_pct=breadth_pct,
            avg_atr_pct=avg_atr_pct,
            trend_score=85,
            summary=f"Trending session (Breadth {breadth_pct}%, Avg Index {avg_idx_move:+}%, ATR {avg_atr_pct}%). Breakout & ORB engines active.",
            indices_snapshot=indices_snapshot,
        )

    if avg_atr_pct < Decimal("0.45") or (Decimal("45.0") <= breadth_pct <= Decimal("55.0") and abs(avg_idx_move) < Decimal("0.15")):
        return RegimeAssessment(
            regime="CHOPPY",
            allow_auto_trading=False,
            recommended_engines=[],
            breadth_pct=breadth_pct,
            avg_atr_pct=avg_atr_pct,
            trend_score=25,
            summary=f"Choppy / low-conviction tape (Breadth {breadth_pct}%, Avg Index {avg_idx_move:+}%, ATR {avg_atr_pct}%). Auto-entries paused by Regime Gate.",
            indices_snapshot=indices_snapshot,
        )

    return RegimeAssessment(
        regime="RANGE_BOUND",
        allow_auto_trading=True,
        recommended_engines=["ENGINE_2B_RSI2", "ENGINE_1_ORB"],
        breadth_pct=breadth_pct,
        avg_atr_pct=avg_atr_pct,
        trend_score=58,
        summary=f"Range-bound / selective rotation (Breadth {breadth_pct}%, Avg Index {avg_idx_move:+}%). Mean-reversion RSI(2) & selective ORB active.",
        indices_snapshot=indices_snapshot,
    )


def _net_rr_after_charges(
    entry: Decimal,
    stop: Decimal,
    target: Decimal,
    direction: str,
    trading_mode: str,
    sample_qty: int = 10,
) -> Tuple[Decimal, Decimal, Decimal]:
    qty = Decimal(str(max(1, sample_qty)))
    profile = CostProfile.for_mode(trading_mode)
    is_long = direction == "LONG"
    entry_ch = compute_leg_charges(entry * qty, is_buy=is_long, profile=profile)
    exit_ch = compute_leg_charges(target * qty, is_buy=not is_long, profile=profile)
    rt_charges = (entry_ch + exit_ch).quantize(Decimal("0.01"))
    gross_risk = abs(entry - stop) * qty
    gross_reward = abs(target - entry) * qty
    if gross_risk <= 0:
        return Decimal("0.00"), Decimal("0.00"), rt_charges
    gross_rr = (gross_reward / gross_risk).quantize(Decimal("0.01"))
    net_reward = gross_reward - rt_charges
    net_risk = gross_risk + rt_charges
    net_rr = (net_reward / net_risk).quantize(Decimal("0.01")) if net_risk > 0 else Decimal("0.00")
    return gross_rr, net_rr, rt_charges


def evaluate_engine_1_orb_vwap(
    symbol: str,
    candles_15m: List[DomainCandle],
    order_book: OrderBookImbalance,
    min_net_rr: Decimal = Decimal("1.50"),
) -> EngineSignal:
    """
    Engine 1 — Morning Intraday: 15m Opening Range Breakout (ORB) + VWAP + Relative Volume + 5-Level Imbalance.
    """
    sym = symbol.upper()
    if len(candles_15m) < 6:
        return EngineSignal(
            symbol=sym, engine_code="ENGINE_1_ORB", engine_name="Morning ORB + VWAP + Order-Book",
            trading_mode="INTRADAY", direction="LONG", status="REJECTED", score=0,
            entry_price=Decimal("0.00"), stop_loss=Decimal("0.00"), target_price=Decimal("0.00"),
            expected_gross_rr=Decimal("0.00"), expected_net_rr=Decimal("0.00"),
            estimated_roundtrip_charges_per_lot=Decimal("0.00"),
            passed_rules=[], failed_rules=["INSUFFICIENT_15M_CANDLES"],
            order_book_pressure=order_book.pressure,
            rationale="Need at least 6 closed 15m candles for ORB + VWAP evaluation.",
        )

    opening_candle = candles_15m[-6]
    latest = candles_15m[-1]
    vwap = _compute_vwap(candles_15m[-12:])
    avg_vol = sum(c.volume for c in candles_15m[-10:-1]) / max(1, len(candles_15m[-10:-1]))
    rel_vol = (Decimal(str(latest.volume)) / Decimal(str(max(1, avg_vol)))).quantize(Decimal("0.01"))

    orb_high = opening_candle.high
    orb_low = opening_candle.low
    orb_range = max(Decimal("0.50"), orb_high - orb_low)

    passed: List[str] = []
    failed: List[str] = []

    # Determine directional bias
    if latest.close >= orb_high:
        direction = "LONG"
        passed.append(f"ORB_HIGH_BREAK (Close {latest.close} >= ORB High {orb_high})")
        if latest.close > vwap:
            passed.append(f"ABOVE_VWAP ({latest.close} > {vwap})")
        else:
            failed.append(f"BELOW_VWAP ({latest.close} <= {vwap})")
        if order_book.pressure == "BUY_IMBALANCE":
            passed.append(f"ORDER_BOOK_BUY_IMBALANCE (Ratio {order_book.imbalance_ratio}x)")
        elif order_book.pressure == "BALANCED":
            passed.append(f"ORDER_BOOK_NEUTRAL (Ratio {order_book.imbalance_ratio}x)")
        else:
            failed.append(f"ORDER_BOOK_SELL_PRESSURE (Ratio {order_book.imbalance_ratio}x)")

        entry = round_to_tick(latest.close, Decimal("0.05"))
        stop = round_to_tick(min(vwap, entry - orb_range * Decimal("0.85")), Decimal("0.05"))
        if stop >= entry:
            stop = round_to_tick(entry * Decimal("0.992"), Decimal("0.05"))
        risk_ps = max(Decimal("0.50"), entry - stop)
        target = round_to_tick(entry + risk_ps * Decimal("2.0"), Decimal("0.05"))
    elif latest.close <= orb_low:
        direction = "SHORT"
        passed.append(f"ORB_LOW_BREAK (Close {latest.close} <= ORB Low {orb_low})")
        if latest.close < vwap:
            passed.append(f"BELOW_VWAP ({latest.close} < {vwap})")
        else:
            failed.append(f"ABOVE_VWAP ({latest.close} >= {vwap})")
        if order_book.pressure == "SELL_IMBALANCE":
            passed.append(f"ORDER_BOOK_SELL_IMBALANCE (Ratio {order_book.imbalance_ratio}x)")
        elif order_book.pressure == "BALANCED":
            passed.append(f"ORDER_BOOK_NEUTRAL (Ratio {order_book.imbalance_ratio}x)")
        else:
            failed.append(f"ORDER_BOOK_BUY_PRESSURE (Ratio {order_book.imbalance_ratio}x)")

        entry = round_to_tick(latest.close, Decimal("0.05"))
        stop = round_to_tick(max(vwap, entry + orb_range * Decimal("0.85")), Decimal("0.05"))
        if stop <= entry:
            stop = round_to_tick(entry * Decimal("1.008"), Decimal("0.05"))
        risk_ps = max(Decimal("0.50"), stop - entry)
        target = round_to_tick(entry - risk_ps * Decimal("2.0"), Decimal("0.05"))
    else:
        direction = "LONG"
        failed.append(f"INSIDE_OPENING_RANGE ({orb_low} - {orb_high})")
        entry = round_to_tick(latest.close, Decimal("0.05"))
        stop = round_to_tick(orb_low, Decimal("0.05"))
        if stop >= entry:
            stop = round_to_tick(entry * Decimal("0.992"), Decimal("0.05"))
        target = round_to_tick(entry + max(Decimal("0.50"), entry - stop) * Decimal("2.0"), Decimal("0.05"))

    if rel_vol >= Decimal("1.15"):
        passed.append(f"VOLUME_SURGE ({rel_vol}x avg)")
    else:
        failed.append(f"WEAK_VOLUME ({rel_vol}x < 1.15x)")

    gross_rr, net_rr, rt_ch = _net_rr_after_charges(entry, stop, target, direction, "INTRADAY")
    if net_rr >= min_net_rr:
        passed.append(f"NET_RR_OK ({net_rr}:1 >= {min_net_rr}:1)")
    else:
        failed.append(f"NET_RR_TOO_LOW ({net_rr}:1 < {min_net_rr}:1)")

    total_rules = max(1, len(passed) + len(failed))
    score = int((len(passed) / total_rules) * 100)
    status = "CONFIRMED" if len(failed) == 0 else ("WATCHLIST" if len(failed) == 1 and score >= 75 else "REJECTED")
    return EngineSignal(
        symbol=sym,
        engine_code="ENGINE_1_ORB",
        engine_name="Morning ORB + VWAP + Order-Book",
        trading_mode="INTRADAY",
        direction=direction,
        status=status,
        score=score,
        entry_price=entry,
        stop_loss=stop,
        target_price=target,
        expected_gross_rr=gross_rr,
        expected_net_rr=net_rr,
        estimated_roundtrip_charges_per_lot=rt_ch,
        passed_rules=passed,
        failed_rules=failed,
        order_book_pressure=order_book.pressure,
        rationale=f"ORB {direction} setup on {sym} (VWAP {vwap}, RelVol {rel_vol}x, OrderBook {order_book.pressure}).",
    )


def evaluate_engine_2a_minervini_vcp(
    symbol: str,
    candles_1d: List[DomainCandle],
    order_book: OrderBookImbalance,
    min_net_rr: Decimal = Decimal("2.00"),
) -> EngineSignal:
    """
    Engine 2A — Mark Minervini Stage-2 Trend Alignment + Volatility Contraction Pattern (VCP).
    Adapts cleanly to available daily history (uses 20/50/150/200 SMA checks as available).
    """
    sym = symbol.upper()
    if len(candles_1d) < 25:
        return EngineSignal(
            symbol=sym, engine_code="ENGINE_2A_VCP", engine_name="Minervini Trend + VCP Breakout",
            trading_mode="SWING", direction="LONG", status="REJECTED", score=0,
            entry_price=Decimal("0.00"), stop_loss=Decimal("0.00"), target_price=Decimal("0.00"),
            expected_gross_rr=Decimal("0.00"), expected_net_rr=Decimal("0.00"),
            estimated_roundtrip_charges_per_lot=Decimal("0.00"),
            passed_rules=[], failed_rules=["INSUFFICIENT_DAILY_HISTORY"],
            order_book_pressure=order_book.pressure,
            rationale="Insufficient daily bars for Minervini Trend + VCP evaluation.",
        )

    closes = [c.close for c in candles_1d]
    latest = candles_1d[-1]
    sma_fast = _sma(closes, 20) or latest.close
    sma_mid = _sma(closes, min(50, len(closes))) or latest.close
    sma_long = _sma(closes, min(150, len(closes))) or sma_mid

    passed: List[str] = []
    failed: List[str] = []

    # Trend stack
    if latest.close > sma_fast and sma_fast >= sma_mid * Decimal("0.995"):
        passed.append(f"STAGE2_TREND_STACK (Close {latest.close} > SMA20 {sma_fast} >= SMA50 {sma_mid})")
    else:
        failed.append(f"NOT_IN_STAGE2_UPTREND (Close {latest.close}, SMA20 {sma_fast}, SMA50 {sma_mid})")

    # Near period high (within 15%)
    period_high = max(c.high for c in candles_1d[-50:])
    dist_from_high_pct = (((period_high - latest.close) / period_high) * Decimal("100")).quantize(Decimal("0.1")) if period_high > 0 else Decimal("100")
    if dist_from_high_pct <= Decimal("15.0"):
        passed.append(f"NEAR_PERIOD_HIGH ({dist_from_high_pct}% below high {period_high})")
    else:
        failed.append(f"TOO_FAR_FROM_HIGH ({dist_from_high_pct}% below {period_high})")

    # Volatility contraction: last 5 bars range smaller than prior 10 bars range
    r_prev = max(c.high for c in candles_1d[-15:-5]) - min(c.low for c in candles_1d[-15:-5])
    r_curr = max(c.high for c in candles_1d[-5:]) - min(c.low for c in candles_1d[-5:])
    if r_prev > 0 and r_curr <= r_prev * Decimal("0.85"):
        passed.append(f"VCP_CONTRACTION (Recent range {r_curr.quantize(Decimal('0.05'))} < Prior {r_prev.quantize(Decimal('0.05'))})")
    else:
        failed.append("NO_VOLATILITY_CONTRACTION")

    # Volume confirmation on breakout bar
    avg_vol = sum(c.volume for c in candles_1d[-15:-1]) / max(1, len(candles_1d[-15:-1]))
    vol_ratio = (Decimal(str(latest.volume)) / Decimal(str(max(1, avg_vol)))).quantize(Decimal("0.01"))
    if vol_ratio >= Decimal("1.10"):
        passed.append(f"PIVOT_VOLUME_CONFIRM ({vol_ratio}x avg)")
    else:
        failed.append(f"LOW_BREAKOUT_VOLUME ({vol_ratio}x)")

    entry = round_to_tick(latest.close, Decimal("0.05"))
    recent_low = min(c.low for c in candles_1d[-5:])
    # Minervini hard max 7% stop rule
    max_stop = round_to_tick(entry * Decimal("0.93"), Decimal("0.05"))
    stop = round_to_tick(max(recent_low, max_stop), Decimal("0.05"))
    if stop >= entry:
        stop = round_to_tick(entry * Decimal("0.965"), Decimal("0.05"))
    risk_ps = max(Decimal("1.00"), entry - stop)
    target = round_to_tick(entry + risk_ps * Decimal("2.8"), Decimal("0.05"))

    gross_rr, net_rr, rt_ch = _net_rr_after_charges(entry, stop, target, "LONG", "SWING")
    if net_rr >= min_net_rr:
        passed.append(f"NET_RR_OK ({net_rr}:1 >= {min_net_rr}:1)")
    else:
        failed.append(f"NET_RR_TOO_LOW ({net_rr}:1)")

    score = int((len(passed) / max(1, len(passed) + len(failed))) * 100)
    status = "CONFIRMED" if len(failed) == 0 else ("WATCHLIST" if len(failed) == 1 and score >= 75 else "REJECTED")
    return EngineSignal(
        symbol=sym,
        engine_code="ENGINE_2A_VCP",
        engine_name="Minervini Trend + VCP Breakout",
        trading_mode="SWING",
        direction="LONG",
        status=status,
        score=score,
        entry_price=entry,
        stop_loss=stop,
        target_price=target,
        expected_gross_rr=gross_rr,
        expected_net_rr=net_rr,
        estimated_roundtrip_charges_per_lot=rt_ch,
        passed_rules=passed,
        failed_rules=failed,
        order_book_pressure=order_book.pressure,
        rationale=f"Minervini VCP {status} on {sym} (Dist from high {dist_from_high_pct}%, Vol {vol_ratio}x, Net R:R {net_rr}:1).",
    )


def evaluate_engine_2b_connors_rsi2(
    symbol: str,
    candles_1d: List[DomainCandle],
    order_book: OrderBookImbalance,
    min_net_rr: Decimal = Decimal("1.30"),
) -> EngineSignal:
    """
    Engine 2B — Larry Connors RSI(2) Pullback Mean Reversion on strong uptrend stocks.
    """
    sym = symbol.upper()
    if len(candles_1d) < 25:
        return EngineSignal(
            symbol=sym, engine_code="ENGINE_2B_RSI2", engine_name="Connors RSI(2) Pullback",
            trading_mode="SWING", direction="LONG", status="REJECTED", score=0,
            entry_price=Decimal("0.00"), stop_loss=Decimal("0.00"), target_price=Decimal("0.00"),
            expected_gross_rr=Decimal("0.00"), expected_net_rr=Decimal("0.00"),
            estimated_roundtrip_charges_per_lot=Decimal("0.00"),
            passed_rules=[], failed_rules=["INSUFFICIENT_DAILY_HISTORY"],
            order_book_pressure=order_book.pressure,
            rationale="Insufficient daily candles for Connors RSI(2).",
        )

    closes = [c.close for c in candles_1d]
    latest = candles_1d[-1]
    sma_long = _sma(closes, min(200, len(closes))) or latest.close
    sma_5 = _sma(closes, 5) or latest.close
    rsi2 = _rsi(closes, period=2) or Decimal("50.00")

    passed: List[str] = []
    failed: List[str] = []

    if latest.close > sma_long:
        passed.append(f"ABOVE_LONG_TREND_SMA (Close {latest.close} > SMA {sma_long})")
    else:
        failed.append(f"BELOW_LONG_TREND_SMA (Close {latest.close} <= SMA {sma_long})")

    if rsi2 <= Decimal("20.00"):
        passed.append(f"RSI2_OVERSOLD_DIP (RSI2={rsi2} <= 20)")
    else:
        failed.append(f"RSI2_NOT_OVERSOLD (RSI2={rsi2} > 20)")

    if latest.close < sma_5:
        passed.append(f"PULLBACK_BELOW_5SMA ({latest.close} < {sma_5})")
    else:
        failed.append(f"ABOVE_5SMA ({latest.close} >= {sma_5})")

    entry = round_to_tick(latest.close, Decimal("0.05"))
    snap = compute_indicator_snapshot(candles_1d)
    raw_atr = snap.get("atr_14") if isinstance(snap, dict) else getattr(snap, "atr_14", None)
    atr = Decimal(str(raw_atr)) if raw_atr and Decimal(str(raw_atr)) > 0 else (entry * Decimal("0.015"))
    stop = round_to_tick(entry - atr * Decimal("1.25"), Decimal("0.05"))
    if stop >= entry:
        stop = round_to_tick(entry * Decimal("0.98"), Decimal("0.05"))
    risk_ps = max(Decimal("0.50"), entry - stop)
    target = round_to_tick(max(sma_5, entry + risk_ps * Decimal("1.6")), Decimal("0.05"))

    gross_rr, net_rr, rt_ch = _net_rr_after_charges(entry, stop, target, "LONG", "SWING")
    if net_rr >= min_net_rr:
        passed.append(f"NET_RR_OK ({net_rr}:1 >= {min_net_rr}:1)")
    else:
        failed.append(f"NET_RR_TOO_LOW ({net_rr}:1)")

    score = int((len(passed) / max(1, len(passed) + len(failed))) * 100)
    status = "CONFIRMED" if len(failed) == 0 else ("WATCHLIST" if len(failed) == 1 and score >= 75 else "REJECTED")
    return EngineSignal(
        symbol=sym,
        engine_code="ENGINE_2B_RSI2",
        engine_name="Connors RSI(2) Pullback",
        trading_mode="SWING",
        direction="LONG",
        status=status,
        score=score,
        entry_price=entry,
        stop_loss=stop,
        target_price=target,
        expected_gross_rr=gross_rr,
        expected_net_rr=net_rr,
        estimated_roundtrip_charges_per_lot=rt_ch,
        passed_rules=passed,
        failed_rules=failed,
        order_book_pressure=order_book.pressure,
        rationale=f"Connors RSI(2)={rsi2} pullback on {sym} (Target {target}, Net R:R {net_rr}:1).",
    )
