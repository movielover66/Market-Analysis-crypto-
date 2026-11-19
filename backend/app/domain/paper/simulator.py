"""
TRADEIQ v2.0 — Penny-Accurate ("পাই টু পাই") Paper Fill Simulator & Position Ledger.
Strictly virtual: has zero imports or call paths to any broker order API.
Guarantees:
- Exact Decimal arithmetic for Gross P&L, Itemized NSE Charges, Slippage, and Net P&L.
- Mark-to-Market includes BOTH entry charges AND estimated exit charges so open P&L
  never flips unexpectedly from green to red on close.
- Outcome ("WIN" / "LOSS" / "BREAKEVEN") is strictly determined by `net_pnl_inr` sign.
- Account Equity Invariant: starting_balance + realised_net_pnl + unrealised_net_pnl == total_equity.
"""
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, List, Optional

from ..risk.risk_engine import CostProfile, compute_leg_charges, round_to_tick


@dataclass(frozen=True)
class PaperOrder:
    order_id: str
    user_id: str
    position_id: str
    symbol: str
    side: str        # "BUY" | "SELL"
    order_type: str  # "PAPER_ENTRY" | "PAPER_SL" | "PAPER_TARGET" | "PAPER_EXIT"
    quantity: int
    planned_price: Decimal
    simulated_fill_price: Decimal
    slippage_inr: Decimal
    charges_inr: Decimal
    filled_at_ist: str


@dataclass
class PaperPosition:
    position_id: str
    user_id: str
    plan_id: Optional[str]
    symbol: str
    trading_mode: str
    direction: str  # "LONG" | "SHORT"
    status: str     # "OPEN" | "CLOSED"
    quantity: int
    planned_entry: Decimal
    entry_fill_price: Decimal
    stop_loss: Decimal
    target_price: Decimal
    exit_fill_price: Optional[Decimal] = None
    exit_reason: Optional[str] = None
    entry_charges_inr: Decimal = Decimal("0.00")
    exit_charges_inr: Decimal = Decimal("0.00")
    total_slippage_inr: Decimal = Decimal("0.00")
    gross_pnl_inr: Decimal = Decimal("0.00")
    net_pnl_inr: Decimal = Decimal("0.00")
    r_multiple: Decimal = Decimal("0.00")
    opened_at_ist: str = ""
    closed_at_ist: Optional[str] = None
    # v2.0 tracking fields (all default-safe for backward compatibility)
    engine_code: str = "MANUAL"
    initial_stop_loss: Optional[Decimal] = None
    breakeven_shifted: bool = False
    trailing_stop_active: bool = False
    outcome: Optional[str] = None  # "WIN" | "LOSS" | "BREAKEVEN" strictly from net_pnl_inr
    rationale: str = ""


def _classify_outcome(net_pnl: Decimal) -> str:
    if net_pnl > Decimal("0.00"):
        return "WIN"
    if net_pnl < Decimal("0.00"):
        return "LOSS"
    return "BREAKEVEN"


class PaperTradingSimulator:
    def __init__(self) -> None:
        self.positions: Dict[str, PaperPosition] = {}
        self.orders: List[PaperOrder] = []

    def open_paper_position(
        self,
        user_id: str,
        symbol: str,
        trading_mode: str,
        direction: str,
        quantity: int,
        planned_entry: Decimal,
        stop_loss: Decimal,
        target_price: Decimal,
        plan_id: Optional[str] = None,
        tick_size: Decimal = Decimal("0.05"),
        cost_profile: Optional[CostProfile] = None,
        now_ist_str: str = "2026-11-19T09:30:00+05:30",
        engine_code: str = "MANUAL",
        rationale: str = "",
    ) -> PaperPosition:
        if quantity < 1:
            raise ValueError("Paper position quantity must be >= 1")
        profile = cost_profile or CostProfile.for_mode(trading_mode)
        slip_rate = profile.slippage_bps / Decimal("10000")
        slip_per_share = (planned_entry * slip_rate).quantize(Decimal("0.05"))
        dir_up = direction.upper()
        fill_price = round_to_tick(
            planned_entry + slip_per_share if dir_up == "LONG" else planned_entry - slip_per_share,
            tick_size,
        )
        qty_dec = Decimal(str(quantity))
        turnover = fill_price * qty_dec
        is_buy = dir_up == "LONG"
        charges = compute_leg_charges(turnover, is_buy=is_buy, profile=profile)
        slip_inr = (abs(fill_price - planned_entry) * qty_dec).quantize(Decimal("0.01"))

        pid = str(uuid.uuid4())
        pos = PaperPosition(
            position_id=pid,
            user_id=user_id,
            plan_id=plan_id,
            symbol=symbol.upper(),
            trading_mode=trading_mode.upper(),
            direction=dir_up,
            status="OPEN",
            quantity=quantity,
            planned_entry=planned_entry,
            entry_fill_price=fill_price,
            stop_loss=stop_loss,
            target_price=target_price,
            entry_charges_inr=charges,
            total_slippage_inr=slip_inr,
            opened_at_ist=now_ist_str,
            engine_code=engine_code.upper(),
            initial_stop_loss=stop_loss,
            rationale=rationale,
        )
        self.positions[pid] = pos
        self.orders.append(
            PaperOrder(
                order_id=str(uuid.uuid4()),
                user_id=user_id,
                position_id=pid,
                symbol=pos.symbol,
                side="BUY" if is_buy else "SELL",
                order_type="PAPER_ENTRY",
                quantity=quantity,
                planned_price=planned_entry,
                simulated_fill_price=fill_price,
                slippage_inr=slip_inr,
                charges_inr=charges,
                filled_at_ist=now_ist_str,
            )
        )
        return pos

    def close_paper_position(
        self,
        user_id: str,
        position_id: str,
        planned_exit_price: Decimal,
        exit_reason: str = "TARGET_HIT",
        tick_size: Decimal = Decimal("0.05"),
        cost_profile: Optional[CostProfile] = None,
        now_ist_str: str = "2026-11-19T14:15:00+05:30",
    ) -> PaperPosition:
        pos = self.positions.get(position_id)
        if not pos or pos.user_id != user_id:
            raise KeyError("Paper position not found for user")
        if pos.status == "CLOSED":
            return pos

        profile = cost_profile or CostProfile.for_mode(pos.trading_mode)
        slip_rate = profile.slippage_bps / Decimal("10000")
        slip_ps = (planned_exit_price * slip_rate).quantize(Decimal("0.05"))
        exit_fill = round_to_tick(
            planned_exit_price - slip_ps if pos.direction == "LONG" else planned_exit_price + slip_ps,
            tick_size,
        )
        qty_dec = Decimal(str(pos.quantity))
        turnover = exit_fill * qty_dec
        is_buy_exit = pos.direction == "SHORT"
        exit_charges = compute_leg_charges(turnover, is_buy=is_buy_exit, profile=profile)
        exit_slip_inr = (abs(planned_exit_price - exit_fill) * qty_dec).quantize(Decimal("0.01"))

        ref_sl = pos.initial_stop_loss if pos.initial_stop_loss is not None else pos.stop_loss
        if pos.direction == "LONG":
            gross = ((exit_fill - pos.entry_fill_price) * qty_dec).quantize(Decimal("0.01"))
            init_risk = max(Decimal("0.01"), (pos.entry_fill_price - ref_sl) * qty_dec)
        else:
            gross = ((pos.entry_fill_price - exit_fill) * qty_dec).quantize(Decimal("0.01"))
            init_risk = max(Decimal("0.01"), (ref_sl - pos.entry_fill_price) * qty_dec)

        total_charges = (pos.entry_charges_inr + exit_charges).quantize(Decimal("0.01"))
        net = (gross - total_charges).quantize(Decimal("0.01"))
        r_mult = (net / init_risk).quantize(Decimal("0.02"))

        pos.status = "CLOSED"
        pos.exit_fill_price = exit_fill
        pos.exit_reason = exit_reason
        pos.exit_charges_inr = exit_charges
        pos.total_slippage_inr = (pos.total_slippage_inr + exit_slip_inr).quantize(Decimal("0.01"))
        pos.gross_pnl_inr = gross
        pos.net_pnl_inr = net
        pos.r_multiple = r_mult
        pos.outcome = _classify_outcome(net)
        pos.closed_at_ist = now_ist_str

        self.orders.append(
            PaperOrder(
                order_id=str(uuid.uuid4()),
                user_id=user_id,
                position_id=position_id,
                symbol=pos.symbol,
                side="BUY" if is_buy_exit else "SELL",
                order_type="PAPER_EXIT",
                quantity=pos.quantity,
                planned_price=planned_exit_price,
                simulated_fill_price=exit_fill,
                slippage_inr=exit_slip_inr,
                charges_inr=exit_charges,
                filled_at_ist=now_ist_str,
            )
        )
        return pos

    def mark_to_market(
        self,
        position: PaperPosition,
        current_ltp: Decimal,
        cost_profile: Optional[CostProfile] = None,
    ) -> Dict[str, Any]:
        """
        Penny-accurate live mark-to-market:
        Includes BOTH entry charges AND estimated exit charges at current_ltp,
        so unrealised_net_inr matches what you would actually pocket if closed right now.
        """
        qty_dec = Decimal(str(position.quantity))
        profile = cost_profile or CostProfile.for_mode(position.trading_mode)
        if position.direction == "LONG":
            unreal_gross = ((current_ltp - position.entry_fill_price) * qty_dec).quantize(Decimal("0.01"))
        else:
            unreal_gross = ((position.entry_fill_price - current_ltp) * qty_dec).quantize(Decimal("0.01"))

        est_exit_charges = compute_leg_charges(
            current_ltp * qty_dec,
            is_buy=(position.direction == "SHORT"),
            profile=profile,
        )
        total_est_charges = (position.entry_charges_inr + est_exit_charges).quantize(Decimal("0.01"))
        unreal_net = (unreal_gross - total_est_charges).quantize(Decimal("0.01"))

        return {
            "current_ltp": current_ltp.quantize(Decimal("0.05")),
            "unrealised_gross_inr": unreal_gross,
            "entry_charges_inr": position.entry_charges_inr.quantize(Decimal("0.01")),
            "estimated_exit_charges_inr": est_exit_charges.quantize(Decimal("0.01")),
            "total_estimated_charges_inr": total_est_charges,
            "unrealised_net_inr": unreal_net,
            "is_profit": unreal_net > Decimal("0.00"),
            "is_loss": unreal_net < Decimal("0.00"),
            "pnl_sign": "+" if unreal_net > 0 else ("-" if unreal_net < 0 else ""),
        }

    def account_ledger(
        self,
        user_id: str,
        starting_balance_inr: Decimal,
        live_ltps: Optional[Dict[str, Decimal]] = None,
    ) -> Dict[str, Any]:
        """
        Full penny-accurate account ledger + per-engine live accuracy breakdown.
        Invariant: total_equity_inr == starting_balance_inr + realised_net_pnl_inr + unrealised_net_pnl_inr.
        """
        ltp_map = {k.upper(): v for k, v in (live_ltps or {}).items()}
        user_positions = [p for p in self.positions.values() if p.user_id == user_id]
        open_pos = [p for p in user_positions if p.status == "OPEN"]
        closed_pos = [p for p in user_positions if p.status == "CLOSED"]

        realised_gross = sum((p.gross_pnl_inr for p in closed_pos), Decimal("0.00")).quantize(Decimal("0.01"))
        realised_charges = sum(((p.entry_charges_inr + p.exit_charges_inr) for p in closed_pos), Decimal("0.00")).quantize(Decimal("0.01"))
        realised_net = sum((p.net_pnl_inr for p in closed_pos), Decimal("0.00")).quantize(Decimal("0.01"))

        unrealised_gross = Decimal("0.00")
        unrealised_charges = Decimal("0.00")
        unrealised_net = Decimal("0.00")
        deployed_capital = Decimal("0.00")
        open_entry_charges = Decimal("0.00")
        live_open_rows: List[Dict[str, Any]] = []

        for p in open_pos:
            ltp = ltp_map.get(p.symbol, p.entry_fill_price)
            mtm = self.mark_to_market(p, ltp)
            unrealised_gross += mtm["unrealised_gross_inr"]
            unrealised_charges += mtm["total_estimated_charges_inr"]
            unrealised_net += mtm["unrealised_net_inr"]
            deployed_capital += (p.entry_fill_price * Decimal(str(p.quantity))).quantize(Decimal("0.01"))
            open_entry_charges += p.entry_charges_inr
            live_open_rows.append({
                "position_id": p.position_id,
                "symbol": p.symbol,
                "engine_code": p.engine_code,
                "trading_mode": p.trading_mode,
                "direction": p.direction,
                "quantity": p.quantity,
                "entry_fill_price": str(p.entry_fill_price),
                "current_ltp": str(mtm["current_ltp"]),
                "stop_loss": str(p.stop_loss),
                "target_price": str(p.target_price),
                "breakeven_shifted": p.breakeven_shifted,
                "trailing_stop_active": p.trailing_stop_active,
                "unrealised_gross_inr": str(mtm["unrealised_gross_inr"]),
                "total_estimated_charges_inr": str(mtm["total_estimated_charges_inr"]),
                "unrealised_net_inr": str(mtm["unrealised_net_inr"]),
                "is_profit": mtm["is_profit"],
                "is_loss": mtm["is_loss"],
                "opened_at_ist": p.opened_at_ist,
                "rationale": p.rationale,
            })

        unrealised_gross = unrealised_gross.quantize(Decimal("0.01"))
        unrealised_charges = unrealised_charges.quantize(Decimal("0.01"))
        unrealised_net = unrealised_net.quantize(Decimal("0.01"))
        total_net_pnl = (realised_net + unrealised_net).quantize(Decimal("0.01"))
        total_equity = (starting_balance_inr + total_net_pnl).quantize(Decimal("0.01"))
        available_cash = (starting_balance_inr + realised_net - deployed_capital - open_entry_charges).quantize(Decimal("0.01"))

        # Strict outcome classification & per-engine accuracy
        wins = [p for p in closed_pos if p.net_pnl_inr > Decimal("0.00")]
        losses = [p for p in closed_pos if p.net_pnl_inr < Decimal("0.00")]
        breakevens = [p for p in closed_pos if p.net_pnl_inr == Decimal("0.00")]
        total_closed = len(closed_pos)
        win_rate = ((Decimal(str(len(wins))) / Decimal(str(total_closed))) * Decimal("100")).quantize(Decimal("0.1")) if total_closed > 0 else Decimal("0.0")

        gross_win_sum = sum((p.net_pnl_inr for p in wins), Decimal("0.00"))
        gross_loss_sum = abs(sum((p.net_pnl_inr for p in losses), Decimal("0.00")))
        profit_factor = (gross_win_sum / gross_loss_sum).quantize(Decimal("0.01")) if gross_loss_sum > 0 else (Decimal("99.99") if gross_win_sum > 0 else Decimal("0.00"))
        expectancy = (realised_net / Decimal(str(total_closed))).quantize(Decimal("0.01")) if total_closed > 0 else Decimal("0.00")

        # Engine breakdown
        engines: Dict[str, List[PaperPosition]] = {}
        for p in closed_pos:
            engines.setdefault(p.engine_code or "MANUAL", []).append(p)
        engine_stats: List[Dict[str, Any]] = []
        for eng_code, plist in sorted(engines.items()):
            ew = [x for x in plist if x.net_pnl_inr > 0]
            el = [x for x in plist if x.net_pnl_inr < 0]
            enet = sum((x.net_pnl_inr for x in plist), Decimal("0.00")).quantize(Decimal("0.01"))
            ech = sum(((x.entry_charges_inr + x.exit_charges_inr) for x in plist), Decimal("0.00")).quantize(Decimal("0.01"))
            ewr = ((Decimal(str(len(ew))) / Decimal(str(len(plist)))) * Decimal("100")).quantize(Decimal("0.1"))
            engine_stats.append({
                "engine_code": eng_code,
                "closed_trades": len(plist),
                "wins": len(ew),
                "losses": len(el),
                "breakevens": len(plist) - len(ew) - len(el),
                "accuracy_pct": str(ewr),
                "charges_inr": str(ech),
                "net_pnl_inr": str(enet),
                "is_profit": enet > 0,
            })

        return {
            "starting_balance_inr": str(starting_balance_inr.quantize(Decimal("0.01"))),
            "available_cash_inr": str(available_cash),
            "deployed_capital_inr": str(deployed_capital.quantize(Decimal("0.01"))),
            "total_equity_inr": str(total_equity),
            "realised_gross_pnl_inr": str(realised_gross),
            "realised_charges_inr": str(realised_charges),
            "realised_net_pnl_inr": str(realised_net),
            "unrealised_gross_pnl_inr": str(unrealised_gross),
            "unrealised_charges_inr": str(unrealised_charges),
            "unrealised_net_pnl_inr": str(unrealised_net),
            "total_net_pnl_inr": str(total_net_pnl),
            "total_net_is_profit": total_net_pnl > 0,
            "total_net_is_loss": total_net_pnl < 0,
            "open_count": len(open_pos),
            "closed_count": total_closed,
            "wins": len(wins),
            "losses": len(losses),
            "breakevens": len(breakevens),
            "accuracy_pct": str(win_rate),
            "profit_factor": str(profit_factor),
            "expectancy_inr": str(expectancy),
            "engine_accuracy": engine_stats,
            "open_positions_live": live_open_rows,
        }
