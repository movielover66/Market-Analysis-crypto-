"""
Milestone 3.5 — Multi-Timeframe Aligner (Section 13).
Evaluates Context (Daily), Setup (1h or 15m), and Trigger (5m or 15m).
A material timeframe conflict or missing context blocks confirmation immediately.
Never averages conflicting signals into a positive score.
"""
from dataclasses import dataclass, field
from typing import Dict, List

from ..candles.validator import DomainCandle
from ..indicators.core import closed_candles_only, ema_series


@dataclass(frozen=True)
class MTFAlignmentResult:
    context_timeframe: str
    setup_timeframe: str
    trigger_timeframe: str
    higher_tf_direction: str  # "BULLISH" | "BEARISH" | "NEUTRAL" | "MISSING"
    setup_direction: str      # "LONG" | "SHORT"
    trigger_confirmed: bool
    has_conflict: bool
    missing_timeframes: List[str] = field(default_factory=list)
    final_status: str = "BLOCKED"  # "ALIGNED" | "BLOCKED_CONFLICT" | "BLOCKED_MISSING_DATA"
    reason: str = ""


def evaluate_mtf_alignment(
    candles_by_tf: Dict[str, List[DomainCandle]],
    setup_direction: str,
    context_tf: str = "1d",
    setup_tf: str = "15m",
    trigger_tf: str = "5m",
) -> MTFAlignmentResult:
    missing: List[str] = []
    ctx_bars = closed_candles_only(candles_by_tf.get(context_tf) or [])
    stp_bars = closed_candles_only(candles_by_tf.get(setup_tf) or [])
    trg_bars = closed_candles_only(candles_by_tf.get(trigger_tf) or [])

    if len(ctx_bars) < 20:
        missing.append(context_tf)
    if len(stp_bars) < 20:
        missing.append(setup_tf)
    if len(trg_bars) < 5:
        missing.append(trigger_tf)

    if missing:
        return MTFAlignmentResult(
            context_timeframe=context_tf,
            setup_timeframe=setup_tf,
            trigger_timeframe=trigger_tf,
            higher_tf_direction="MISSING",
            setup_direction=setup_direction,
            trigger_confirmed=False,
            has_conflict=True,
            missing_timeframes=missing,
            final_status="BLOCKED_MISSING_DATA",
            reason=f"Missing or insufficient closed candles for timeframes: {', '.join(missing)}",
        )

    ctx_closes = [c.close for c in ctx_bars]
    ctx_ema20 = ema_series(ctx_closes, 20)[-1] or ctx_closes[-1]
    if ctx_closes[-1] > ctx_ema20:
        htf_dir = "BULLISH"
    elif ctx_closes[-1] < ctx_ema20:
        htf_dir = "BEARISH"
    else:
        htf_dir = "NEUTRAL"

    last_trg = trg_bars[-1]
    if setup_direction == "LONG":
        conflict = htf_dir == "BEARISH"
        trg_ok = last_trg.close >= last_trg.open
    else:
        conflict = htf_dir == "BULLISH"
        trg_ok = last_trg.close <= last_trg.open

    if conflict:
        return MTFAlignmentResult(
            context_timeframe=context_tf,
            setup_timeframe=setup_tf,
            trigger_timeframe=trigger_tf,
            higher_tf_direction=htf_dir,
            setup_direction=setup_direction,
            trigger_confirmed=trg_ok,
            has_conflict=True,
            missing_timeframes=[],
            final_status="BLOCKED_CONFLICT",
            reason=f"Higher timeframe ({context_tf}) is {htf_dir}, which conflicts with {setup_direction} setup.",
        )

    if not trg_ok:
        return MTFAlignmentResult(
            context_timeframe=context_tf,
            setup_timeframe=setup_tf,
            trigger_timeframe=trigger_tf,
            higher_tf_direction=htf_dir,
            setup_direction=setup_direction,
            trigger_confirmed=False,
            has_conflict=True,
            missing_timeframes=[],
            final_status="BLOCKED_CONFLICT",
            reason=f"Trigger timeframe ({trigger_tf}) last closed candle did not confirm {setup_direction} momentum.",
        )

    return MTFAlignmentResult(
        context_timeframe=context_tf,
        setup_timeframe=setup_tf,
        trigger_timeframe=trigger_tf,
        higher_tf_direction=htf_dir,
        setup_direction=setup_direction,
        trigger_confirmed=True,
        has_conflict=False,
        missing_timeframes=[],
        final_status="ALIGNED",
        reason=f"{context_tf} ({htf_dir}), {setup_tf}, and {trigger_tf} are aligned.",
    )
