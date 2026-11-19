"""
Phase 5 (Milestones 5.1 & 5.2) — Pure Paper Fill Simulator & Position Ledger.
Strictly virtual: has zero imports or call paths to any broker order API.
Applies configured slippage and itemized NSE charges accurately using Decimal.
"""
import datetime
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, List, Optional

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
        now_ist_str: str = "2026-11-18T09:30:00+05:30",
    ) -> PaperPosition:
        if quantity < 1:
            raise ValueError("Paper position quantity must be >= 1")
        profile = cost_profile or CostProfile.for_mode(trading_mode)
        slip_rate = profile.slippage_bps / Decimal("10000")
        slip_per_share = (planned_entry * slip_rate).quantize(Decimal("0.05"))
        fill_price = round_to_tick(
            planned_entry + slip_per_share if direction == "LONG" else planned_entry - slip_per_share,
            tick_size,
        )
        qty_dec = Decimal(str(quantity))
        turnover = fill_price * qty_dec
        is_buy = direction == "LONG"
        charges = compute_leg_charges(turnover, is_buy=is_buy, profile=profile)
        slip_inr = (abs(fill_price - planned_entry) * qty_dec).quantize(Decimal("0.01"))

        pid = str(uuid.uuid4())
        pos = PaperPosition(
            position_id=pid,
            user_id=user_id,
            plan_id=plan_id,
            symbol=symbol.upper(),
            trading_mode=trading_mode.upper(),
            direction=direction.upper(),
            status="OPEN",
            quantity=quantity,
            planned_entry=planned_entry,
            entry_fill_price=fill_price,
            stop_loss=stop_loss,
            target_price=target_price,
            entry_charges_inr=charges,
            total_slippage_inr=slip_inr,
            opened_at_ist=now_ist_str,
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
        now_ist_str: str = "2026-11-18T14:15:00+05:30",
    ) -> PaperPosition:
        pos = self.positions.get(position_id)
        if not pos or pos.user_id != user_id:
            raise KeyError("Paper position not found for user")
        if pos.status == "CLOSED":
            return pos

        profile = cost_profile or CostProfile.for_mode(pos.trading_mode)
        slip_rate = profile.slippage_bps / Decimal("10000")
        slip_ps = (planned_exit_price * slip_rate).quantize(Decimal("0.05"))
        # Adverse exit slippage: lower fill when selling LONG, higher fill when covering SHORT
        exit_fill = round_to_tick(
            planned_exit_price - slip_ps if pos.direction == "LONG" else planned_exit_price + slip_ps,
            tick_size,
        )
        qty_dec = Decimal(str(pos.quantity))
        turnover = exit_fill * qty_dec
        is_buy_exit = pos.direction == "SHORT"
        exit_charges = compute_leg_charges(turnover, is_buy=is_buy_exit, profile=profile)
        exit_slip_inr = (abs(planned_exit_price - exit_fill) * qty_dec).quantize(Decimal("0.01"))

        if pos.direction == "LONG":
            gross = ((exit_fill - pos.entry_fill_price) * qty_dec).quantize(Decimal("0.01"))
            init_risk = max(Decimal("0.01"), (pos.entry_fill_price - pos.stop_loss) * qty_dec)
        else:
            gross = ((pos.entry_fill_price - exit_fill) * qty_dec).quantize(Decimal("0.01"))
            init_risk = max(Decimal("0.01"), (pos.stop_loss - pos.entry_fill_price) * qty_dec)

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

    def mark_to_market(self, position: PaperPosition, current_ltp: Decimal) -> Dict[str, Decimal]:
        qty_dec = Decimal(str(position.quantity))
        if position.direction == "LONG":
            unreal_gross = ((current_ltp - position.entry_fill_price) * qty_dec).quantize(Decimal("0.01"))
        else:
            unreal_gross = ((position.entry_fill_price - current_ltp) * qty_dec).quantize(Decimal("0.01"))
        unreal_net = (unreal_gross - position.entry_charges_inr).quantize(Decimal("0.01"))
        return {"unrealised_gross_inr": unreal_gross, "unrealised_net_inr": unreal_net}
