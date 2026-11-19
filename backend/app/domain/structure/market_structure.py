"""
Milestone 3.2 — Pure Market Structure, Confirmed Pivots & Support/Resistance Zones.
Strict Look-Ahead Invariance: A pivot at bar index `i` with confirmation lag `k`
only becomes visible once bar `i + k` has closed (`closed_len > i + k`).
"""
from dataclasses import dataclass
from decimal import Decimal
from typing import List, Optional, Tuple

from ..candles.validator import DomainCandle
from ..indicators.core import closed_candles_only, wilder_atr_series


@dataclass(frozen=True)
class PivotPoint:
    bar_index: int
    confirmed_at_index: int
    price: Decimal
    kind: str  # "HIGH" | "LOW"


@dataclass(frozen=True)
class SupportResistanceZone:
    kind: str  # "SUPPORT" | "RESISTANCE"
    center: Decimal
    lower: Decimal
    upper: Decimal
    touch_count: int
    strength_score: int


def confirmed_pivots(candles: List[DomainCandle], k: int = 3) -> Tuple[List[PivotPoint], List[PivotPoint]]:
    """
    Finds swing highs and swing lows using only closed candles up to the end of `candles`.
    A pivot at index `i` requires `k` closed bars to its left AND `k` closed bars to its right.
    Future bars beyond `i + k` never change whether `i` is a pivot (look-ahead invariant).
    """
    closed = closed_candles_only(candles)
    n = len(closed)
    highs: List[PivotPoint] = []
    lows: List[PivotPoint] = []
    if n < 2 * k + 1:
        return highs, lows

    for i in range(k, n - k):
        window = closed[i - k : i + k + 1]
        hi_val = closed[i].high
        lo_val = closed[i].low
        if all(hi_val >= x.high for x in window) and any(hi_val > x.high for x in window):
            highs.append(PivotPoint(bar_index=i, confirmed_at_index=i + k, price=hi_val, kind="HIGH"))
        if all(lo_val <= x.low for x in window) and any(lo_val < x.low for x in window):
            lows.append(PivotPoint(bar_index=i, confirmed_at_index=i + k, price=lo_val, kind="LOW"))
    return highs, lows


def classify_trend_structure(candles: List[DomainCandle], k: int = 3) -> str:
    """Returns UPTREND_HH_HL | DOWNTREND_LH_LL | RANGE_SIDEWAYS."""
    highs, lows = confirmed_pivots(candles, k=k)
    if len(highs) >= 2 and len(lows) >= 2:
        hh = highs[-1].price > highs[-2].price
        hl = lows[-1].price > lows[-2].price
        lh = highs[-1].price < highs[-2].price
        ll = lows[-1].price < lows[-2].price
        if hh and hl:
            return "UPTREND_HH_HL"
        if lh and ll:
            return "DOWNTREND_LH_LL"
    return "RANGE_SIDEWAYS"


def build_sr_zones(
    candles: List[DomainCandle],
    k: int = 3,
    atr_band_mult: Decimal = Decimal("0.35"),
) -> List[SupportResistanceZone]:
    closed = closed_candles_only(candles)
    if len(closed) < 2 * k + 1:
        return []
    atrs = wilder_atr_series(closed, 14)
    atr_val = atrs[-1] or (closed[-1].close * Decimal("0.01"))
    half_width = (atr_val * atr_band_mult).quantize(Decimal("0.05"))
    if half_width <= Decimal("0"):
        half_width = Decimal("0.50")

    highs, lows = confirmed_pivots(closed, k=k)
    last_close = closed[-1].close
    zones: List[SupportResistanceZone] = []

    def _cluster(points: List[PivotPoint], kind: str) -> None:
        clusters: List[List[Decimal]] = []
        for p in points:
            placed = False
            for cl in clusters:
                avg = sum(cl, Decimal("0")) / Decimal(str(len(cl)))
                if abs(p.price - avg) <= half_width * Decimal("2"):
                    cl.append(p.price)
                    placed = True
                    break
            if not placed:
                clusters.append([p.price])

        for cl in clusters:
            center = (sum(cl, Decimal("0")) / Decimal(str(len(cl)))).quantize(Decimal("0.05"))
            touches = len(cl)
            zones.append(
                SupportResistanceZone(
                    kind=kind,
                    center=center,
                    lower=(center - half_width).quantize(Decimal("0.05")),
                    upper=(center + half_width).quantize(Decimal("0.05")),
                    touch_count=touches,
                    strength_score=min(100, touches * 35),
                )
            )

    _cluster([h for h in highs if h.price >= last_close * Decimal("0.98")], "RESISTANCE")
    _cluster([l for l in lows if l.price <= last_close * Decimal("1.02")], "SUPPORT")
    return sorted(zones, key=lambda z: z.center)
