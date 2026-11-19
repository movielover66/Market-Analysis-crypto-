"""
Phase 5 (Milestones 5.3 & 5.4) — Trading Journal & Performance Analytics Engine.
Computes reconciled gross vs net P&L, win rate, expectancy, profit factor,
maximum drawdown, average R, and rule-adherence statistics.
"""
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class PerformanceSummary:
    trade_count: int
    win_count: int
    loss_count: int
    win_rate_pct: Decimal
    avg_win_inr: Decimal
    avg_loss_inr: Decimal
    expectancy_inr: Decimal
    profit_factor: Optional[Decimal]
    max_drawdown_inr: Decimal
    avg_r: Decimal
    gross_pnl_inr: Decimal
    total_charges_inr: Decimal
    net_pnl_inr: Decimal
    rule_adherence_pct: Decimal
    by_strategy: Dict[str, Dict[str, str]]
    by_month: Dict[str, str]


def compute_performance_analytics(closed_records: List[Dict[str, Any]]) -> PerformanceSummary:
    n = len(closed_records)
    if n == 0:
        z = Decimal("0.00")
        return PerformanceSummary(0, 0, 0, z, z, z, z, None, z, z, z, z, z, Decimal("100.00"), {}, {})

    wins: List[Decimal] = []
    losses: List[Decimal] = []
    gross_total = Decimal("0.00")
    charges_total = Decimal("0.00")
    net_total = Decimal("0.00")
    r_total = Decimal("0.00")
    adhered_count = 0

    equity = Decimal("0.00")
    peak_equity = Decimal("0.00")
    max_dd = Decimal("0.00")

    by_strat: Dict[str, Dict[str, Any]] = {}
    by_month: Dict[str, Decimal] = {}

    for rec in closed_records:
        gross = Decimal(str(rec.get("gross_pnl_inr", "0.00")))
        charges = Decimal(str(rec.get("charges_inr", "0.00")))
        net = Decimal(str(rec.get("net_pnl_inr", gross - charges)))
        r_val = Decimal(str(rec.get("r_multiple", "0.00")))
        strat = str(rec.get("strategy_code") or "MANUAL")
        month_key = str(rec.get("closed_at_ist") or "2026-11")[:7]

        if rec.get("rule_adherence", True):
            adhered_count += 1

        gross_total += gross
        charges_total += charges
        net_total += net
        r_total += r_val

        if net > Decimal("0"):
            wins.append(net)
        else:
            losses.append(net)

        equity += net
        if equity > peak_equity:
            peak_equity = equity
        dd = peak_equity - equity
        if dd > max_dd:
            max_dd = dd

        st_bucket = by_strat.setdefault(strat, {"count": 0, "wins": 0, "net_pnl": Decimal("0.00")})
        st_bucket["count"] += 1
        if net > Decimal("0"):
            st_bucket["wins"] += 1
        st_bucket["net_pnl"] += net

        by_month[month_key] = by_month.get(month_key, Decimal("0.00")) + net

    n_dec = Decimal(str(n))
    win_rate = (Decimal(str(len(wins))) / n_dec * Decimal("100")).quantize(Decimal("0.01"))
    avg_win = (sum(wins, Decimal("0.00")) / Decimal(str(len(wins)))).quantize(Decimal("0.01")) if wins else Decimal("0.00")
    avg_loss = (sum(losses, Decimal("0.00")) / Decimal(str(len(losses)))).quantize(Decimal("0.01")) if losses else Decimal("0.00")
    expectancy = (net_total / n_dec).quantize(Decimal("0.01"))

    sum_wins = sum(wins, Decimal("0.00"))
    sum_abs_losses = abs(sum(losses, Decimal("0.00")))
    pf = (sum_wins / sum_abs_losses).quantize(Decimal("0.02")) if sum_abs_losses > Decimal("0") else None

    strat_out = {
        k: {
            "count": str(v["count"]),
            "win_rate_pct": str((Decimal(str(v["wins"])) / Decimal(str(v["count"])) * Decimal("100")).quantize(Decimal("0.01"))),
            "net_pnl_inr": str(v["net_pnl"].quantize(Decimal("0.01"))),
        }
        for k, v in by_strat.items()
    }
    month_out = {k: str(v.quantize(Decimal("0.01"))) for k, v in by_month.items()}

    return PerformanceSummary(
        trade_count=n,
        win_count=len(wins),
        loss_count=len(losses),
        win_rate_pct=win_rate,
        avg_win_inr=avg_win,
        avg_loss_inr=avg_loss,
        expectancy_inr=expectancy,
        profit_factor=pf,
        max_drawdown_inr=max_dd.quantize(Decimal("0.01")),
        avg_r=(r_total / n_dec).quantize(Decimal("0.02")),
        gross_pnl_inr=gross_total.quantize(Decimal("0.01")),
        total_charges_inr=charges_total.quantize(Decimal("0.01")),
        net_pnl_inr=net_total.quantize(Decimal("0.01")),
        rule_adherence_pct=(Decimal(str(adhered_count)) / n_dec * Decimal("100")).quantize(Decimal("0.01")),
        by_strategy=strat_out,
        by_month=month_out,
    )
