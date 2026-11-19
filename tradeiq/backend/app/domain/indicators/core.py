"""
Milestone 2.7 — Pure Domain Technical Indicators (Section 10).
Calculated strictly from closed normalized candles (Rule 5).
Indicators:
  - EMA (9, 20, 50, 200)
  - RSI (14, Wilder smoothing)
  - MACD (12, 26, 9)
  - Session VWAP
  - ATR (14, Wilder smoothing)
  - Volume SMA (20) & Relative Volume (RVOL)
"""
from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, List, Optional
from ..candles.validator import DomainCandle


def closed_candles_only(candles: List[DomainCandle]) -> List[DomainCandle]:
    return [c for c in candles if c.is_closed and c.quality_status == "VALID"]


def ema_series(values: List[Decimal], period: int) -> List[Optional[Decimal]]:
    n = len(values)
    if period <= 0 or n < period:
        return [None] * n
    out: List[Optional[Decimal]] = [None] * n
    k = Decimal("2") / Decimal(str(period + 1))
    one_minus_k = Decimal("1") - k
    seed = sum(values[:period], Decimal("0")) / Decimal(str(period))
    out[period - 1] = seed.quantize(Decimal("0.0001"))
    prev = seed
    for i in range(period, n):
        prev = values[i] * k + prev * one_minus_k
        out[i] = prev.quantize(Decimal("0.0001"))
    return out


def wilder_rsi_series(closes: List[Decimal], period: int = 14) -> List[Optional[Decimal]]:
    n = len(closes)
    if period <= 0 or n <= period:
        return [None] * n
    out: List[Optional[Decimal]] = [None] * n
    gains = Decimal("0")
    losses = Decimal("0")
    p_dec = Decimal(str(period))
    p_minus_1 = Decimal(str(period - 1))

    for i in range(1, period + 1):
        diff = closes[i] - closes[i - 1]
        if diff >= 0:
            gains += diff
        else:
            losses -= diff

    avg_gain = gains / p_dec
    avg_loss = losses / p_dec

    def _compute_rsi(ag: Decimal, al: Decimal) -> Decimal:
        if al == 0:
            return Decimal("100.00")
        rs = ag / al
        return (Decimal("100") - (Decimal("100") / (Decimal("1") + rs))).quantize(Decimal("0.01"))

    out[period] = _compute_rsi(avg_gain, avg_loss)
    for i in range(period + 1, n):
        diff = closes[i] - closes[i - 1]
        g = diff if diff > 0 else Decimal("0")
        l = -diff if diff < 0 else Decimal("0")
        avg_gain = (avg_gain * p_minus_1 + g) / p_dec
        avg_loss = (avg_loss * p_minus_1 + l) / p_dec
        out[i] = _compute_rsi(avg_gain, avg_loss)
    return out


@dataclass(frozen=True)
class MACDPoint:
    macd: Optional[Decimal]
    signal: Optional[Decimal]
    histogram: Optional[Decimal]


def macd_series(
    closes: List[Decimal],
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9,
) -> List[MACDPoint]:
    n = len(closes)
    fast_ema = ema_series(closes, fast_period)
    slow_ema = ema_series(closes, slow_period)
    macd_vals: List[Optional[Decimal]] = [None] * n
    valid_macd: List[Decimal] = []
    valid_indices: List[int] = []

    for i in range(n):
        if fast_ema[i] is not None and slow_ema[i] is not None:
            m = (fast_ema[i] - slow_ema[i]).quantize(Decimal("0.0001"))
            macd_vals[i] = m
            valid_macd.append(m)
            valid_indices.append(i)

    sig_sub = ema_series(valid_macd, signal_period)
    sig_vals: List[Optional[Decimal]] = [None] * n
    for idx, s_val in zip(valid_indices, sig_sub):
        sig_vals[idx] = s_val

    out: List[MACDPoint] = []
    for i in range(n):
        m = macd_vals[i]
        s = sig_vals[i]
        h = (m - s).quantize(Decimal("0.0001")) if (m is not None and s is not None) else None
        out.append(MACDPoint(macd=m, signal=s, histogram=h))
    return out


def wilder_atr_series(candles: List[DomainCandle], period: int = 14) -> List[Optional[Decimal]]:
    n = len(candles)
    if period <= 0 or n <= period:
        return [None] * n
    out: List[Optional[Decimal]] = [None] * n
    trs: List[Decimal] = []
    for i in range(1, n):
        cur, prev = candles[i], candles[i - 1]
        tr = max(
            cur.high - cur.low,
            abs(cur.high - prev.close),
            abs(cur.low - prev.close),
        )
        trs.append(tr)

    p_dec = Decimal(str(period))
    p_minus_1 = Decimal(str(period - 1))
    atr = sum(trs[:period], Decimal("0")) / p_dec
    out[period] = atr.quantize(Decimal("0.0001"))
    for i in range(period, len(trs)):
        atr = (atr * p_minus_1 + trs[i]) / p_dec
        out[i + 1] = atr.quantize(Decimal("0.0001"))
    return out


def session_vwap_series(candles: List[DomainCandle]) -> List[Optional[Decimal]]:
    """Computes session-anchored VWAP resetting each IST trading date."""
    out: List[Optional[Decimal]] = []
    cur_date = None
    cum_pv = Decimal("0")
    cum_vol = 0

    for c in candles:
        d = c.ts_ist.date()
        if d != cur_date:
            cur_date = d
            cum_pv = Decimal("0")
            cum_vol = 0
        typical = (c.high + c.low + c.close) / Decimal("3")
        cum_pv += typical * Decimal(str(c.volume))
        cum_vol += c.volume
        if cum_vol > 0:
            out.append((cum_pv / Decimal(str(cum_vol))).quantize(Decimal("0.0001")))
        else:
            out.append(c.close)
    return out


def volume_metrics(candles: List[DomainCandle], lookback: int = 20) -> Dict[str, Optional[Decimal]]:
    closed = closed_candles_only(candles)
    if len(closed) < lookback + 1:
        return {"vol_sma_20": None, "rvol": None}
    baseline = closed[-lookback - 1 : -1]
    avg_vol = sum(Decimal(str(x.volume)) for x in baseline) / Decimal(str(lookback))
    if avg_vol <= 0:
        return {"vol_sma_20": Decimal("0"), "rvol": Decimal("1.00")}
    cur_vol = Decimal(str(closed[-1].volume))
    rvol = (cur_vol / avg_vol).quantize(Decimal("0.01"))
    return {"vol_sma_20": avg_vol.quantize(Decimal("0.01")), "rvol": rvol}


def compute_indicator_snapshot(candles: List[DomainCandle]) -> Dict[str, Optional[Decimal]]:
    closed = closed_candles_only(candles)
    if not closed:
        return {}
    closes = [c.close for c in closed]
    e9 = ema_series(closes, 9)[-1]
    e20 = ema_series(closes, 20)[-1]
    e50 = ema_series(closes, 50)[-1]
    e200 = ema_series(closes, 200)[-1]
    rsi14 = wilder_rsi_series(closes, 14)[-1]
    macd_pt = macd_series(closes, 12, 26, 9)[-1]
    atr14 = wilder_atr_series(closed, 14)[-1]
    vwap = session_vwap_series(closed)[-1]
    v_met = volume_metrics(closed, 20)
    return {
        "close": closes[-1],
        "ema_9": e9,
        "ema_20": e20,
        "ema_50": e50,
        "ema_200": e200,
        "rsi_14": rsi14,
        "macd": macd_pt.macd,
        "macd_signal": macd_pt.signal,
        "macd_histogram": macd_pt.histogram,
        "atr_14": atr14,
        "session_vwap": vwap,
        "vol_sma_20": v_met["vol_sma_20"],
        "rvol": v_met["rvol"],
    }
