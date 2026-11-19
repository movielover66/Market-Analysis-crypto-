"""
Milestone 2.5 — 09:15 IST Anchored 5m -> 15m / 30m / 1h Candle Aggregation.
Never uses incomplete buckets as closed candles: incomplete buckets are marked
is_closed=False and quality_status='PROVISIONAL' (Rule 5 of Section 7).
"""
import datetime
from typing import Dict, List
from ..calendar.nse_calendar import to_ist
from .validator import DomainCandle

_BUCKET_MINUTES = {
    "15m": (15, 3),
    "30m": (30, 6),
    "1h": (60, 12),
}


def aggregate_5m_candles(bars_5m: List[DomainCandle], target_tf: str) -> List[DomainCandle]:
    if target_tf not in _BUCKET_MINUTES:
        raise ValueError(f"Unsupported aggregation timeframe {target_tf!r}; expected one of {list(_BUCKET_MINUTES)}")
    if not bars_5m:
        return []

    span_mins, expected_count = _BUCKET_MINUTES[target_tf]
    buckets: Dict[datetime.datetime, List[DomainCandle]] = {}

    for b in bars_5m:
        if b.timeframe != "5m":
            raise ValueError(f"Input bars must be '5m', got {b.timeframe!r}")
        ts = to_ist(b.ts_ist)
        anchor = ts.replace(hour=9, minute=15, second=0, microsecond=0)
        elapsed_mins = int((ts - anchor).total_seconds() // 60)
        if elapsed_mins < 0 or elapsed_mins >= 375:
            # Outside regular 09:15–15:30 IST NSE cash session
            continue
        bucket_idx = elapsed_mins // span_mins
        bucket_start = anchor + datetime.timedelta(minutes=bucket_idx * span_mins)
        buckets.setdefault(bucket_start, []).append(b)

    out: List[DomainCandle] = []
    for b_start in sorted(buckets.keys()):
        group = sorted(buckets[b_start], key=lambda x: x.ts_ist)
        # Note: final 1h bucket of NSE session (15:15–15:30) has 3 x 5m bars (15 mins)
        session_end = b_start.replace(hour=15, minute=30)
        max_possible_bars = min(expected_count, int((session_end - b_start).total_seconds() // 300))
        all_closed = len(group) == max_possible_bars and all(x.is_closed for x in group)

        out.append(
            DomainCandle(
                symbol=group[0].symbol,
                timeframe=target_tf,
                ts_ist=b_start,
                open=group[0].open,
                high=max(x.high for x in group),
                low=min(x.low for x in group),
                close=group[-1].close,
                volume=sum(x.volume for x in group),
                is_closed=all_closed,
                source=group[-1].source,
                freshness=group[-1].freshness,
                quality_status="VALID" if all_closed else "PROVISIONAL",
                adjustment_status=group[-1].adjustment_status,
            )
        )
    return out
