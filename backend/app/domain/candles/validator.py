"""
Milestone 2.4 — Pure Candle Validation & Quarantine Engine.
Never fabricates missing candles; flags invalid OHLCV, duplicates, out-of-order bars, or extreme jumps.
"""
import datetime
from dataclasses import dataclass
from decimal import Decimal
from typing import List, Optional, Tuple


@dataclass(frozen=True)
class DomainCandle:
    symbol: str
    timeframe: str
    ts_ist: datetime.datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    is_closed: bool = True
    source: str = "ANGEL_ONE"
    freshness: str = "HISTORICAL"
    quality_status: str = "VALID"
    adjustment_status: str = "UNADJUSTED"


@dataclass(frozen=True)
class QuarantinedCandle:
    raw_index: int
    reason_code: str
    reason_detail: str


def validate_candle_series(
    raw_candles: List[DomainCandle],
    max_bar_jump_pct: Decimal = Decimal("25.0"),
) -> Tuple[List[DomainCandle], List[QuarantinedCandle]]:
    """
    Validates a sequence of candles and separates VALID/PROVISIONAL bars from QUARANTINED bars.
    Checks:
      1. Positive prices (open, high, low, close > 0)
      2. OHLC invariant: high >= max(open, close) and low <= min(open, close)
      3. Non-negative volume (volume >= 0)
      4. Strictly increasing timestamps (no duplicates or out-of-order bars)
      5. No unadjusted extreme spike (> max_bar_jump_pct in intraday timeframes)
    """
    valid: List[DomainCandle] = []
    quarantined: List[QuarantinedCandle] = []
    last_ts: Optional[datetime.datetime] = None
    last_close: Optional[Decimal] = None

    for idx, c in enumerate(raw_candles):
        if c.open <= 0 or c.high <= 0 or c.low <= 0 or c.close <= 0:
            quarantined.append(QuarantinedCandle(idx, "NON_POSITIVE_PRICE", f"Prices must be > 0 at {c.ts_ist}"))
            continue
        if c.high < max(c.open, c.close) or c.low > min(c.open, c.close):
            quarantined.append(QuarantinedCandle(idx, "INVALID_OHLC_BOUNDS", f"High/Low bounds violated at {c.ts_ist}"))
            continue
        if c.volume < 0:
            quarantined.append(QuarantinedCandle(idx, "NEGATIVE_VOLUME", f"Volume {c.volume} < 0 at {c.ts_ist}"))
            continue
        if last_ts is not None:
            if c.ts_ist == last_ts:
                quarantined.append(QuarantinedCandle(idx, "DUPLICATE_TIMESTAMP", f"Duplicate timestamp {c.ts_ist}"))
                continue
            if c.ts_ist < last_ts:
                quarantined.append(QuarantinedCandle(idx, "OUT_OF_ORDER_TIMESTAMP", f"Timestamp {c.ts_ist} < {last_ts}"))
                continue
        if last_close is not None and c.timeframe in ("5m", "15m", "30m", "1h"):
            jump_pct = abs((c.close - last_close) / last_close) * Decimal("100")
            if jump_pct > max_bar_jump_pct:
                quarantined.append(QuarantinedCandle(idx, "UNEXPECTED_PRICE_JUMP", f"Jump {jump_pct:.2f}% exceeds {max_bar_jump_pct}%"))
                continue

        status = "VALID" if c.is_closed else "PROVISIONAL"
        valid.append(
            DomainCandle(
                symbol=c.symbol,
                timeframe=c.timeframe,
                ts_ist=c.ts_ist,
                open=c.open,
                high=c.high,
                low=c.low,
                close=c.close,
                volume=c.volume,
                is_closed=c.is_closed,
                source=c.source,
                freshness=c.freshness,
                quality_status=status,
                adjustment_status=c.adjustment_status,
            )
        )
        last_ts = c.ts_ist
        last_close = c.close

    return valid, quarantined
