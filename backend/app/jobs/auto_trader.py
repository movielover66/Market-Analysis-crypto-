"""
TRADEIQ v2.0 — 3-Engine Autonomous Paper-Trading Orchestrator.
- Configurable starting balance & per-trade allocation amount (editable live from UI).
- Runs Engine 0 (Regime Gate) -> Engine 1 (Morning ORB/VWAP/5-Level Imbalance),
  Engine 2A (Minervini VCP), Engine 2B (Connors RSI-2).
- Checks Gemini News Sentiment Shield before opening any BUY trade.
- Monitors open positions against LIVE LTP:
  * Shifts SL to Charge-Buffered Breakeven at >= 1.5R (so breakeven stop never closes at -₹10!)
  * Trails SL upward on runners (>= 2.0R)
  * Auto-closes on Target Hit, Stop-Loss Hit, Trailing SL Hit, or 15:15 IST Intraday Square-Off
- Enforces Daily Loss Kill-Switch and 3-Consecutive-SL Circuit Breaker.
- Generates & sends Daily Telegram Summary (targets hit, SLs hit, net P&L, accuracy %).
"""
import datetime
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional

from ..domain.candles.aggregator import DomainCandle
from ..domain.paper.simulator import PaperPosition, PaperTradingSimulator
from ..domain.risk.risk_engine import CostProfile, compute_leg_charges, round_to_tick
from ..domain.strategies.three_engine import (
    EngineSignal,
    RegimeAssessment,
    compute_order_book_imbalance,
    evaluate_engine_1_orb_vwap,
    evaluate_engine_2a_minervini_vcp,
    evaluate_engine_2b_connors_rsi2,
    evaluate_market_regime,
)

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

def get_horizon_meta(engine_code: str, trading_mode: str = "INTRADAY") -> Dict[str, str]:
    """Maps engine/mode to explicit UI bucket and human-readable hold timeframe."""
    ec = (engine_code or "").upper()
    tm = (trading_mode or "").upper()
    if "ENGINE_1" in ec or "ORB" in ec or tm == "INTRADAY":
        return {
            "horizon_bucket": "intraday",
            "horizon_label": "Intraday (Today)",
            "hold_horizon": "Intraday • 2–5 hrs (Auto-Exit 3:15 PM IST)",
        }
    if "ENGINE_2B" in ec or "RSI2" in ec:
        return {
            "horizon_bucket": "swing",
            "horizon_label": "Short Swing",
            "hold_horizon": "Short Swing • 2–4 Trading Days",
        }
    if "ENGINE_2A" in ec or "VCP" in ec:
        return {
            "horizon_bucket": "positional",
            "horizon_label": "Positional / Long",
            "hold_horizon": "Positional • 1–4 Weeks",
        }
    return {
        "horizon_bucket": "swing",
        "horizon_label": "Short Swing",
        "hold_horizon": "Short Swing • 2–4 Trading Days",
    }



@dataclass
class AutoTraderConfig:
    enabled: bool = True
    starting_balance_inr: Decimal = Decimal("100000.00")
    per_trade_amount_inr: Decimal = Decimal("20000.00")
    max_open_positions: int = 4
    daily_loss_limit_pct: Decimal = Decimal("1.50")
    max_consecutive_losses: int = 3
    enable_engine_1_orb: bool = True
    enable_engine_2a_vcp: bool = True
    enable_engine_2b_rsi2: bool = True
    enable_news_shield: bool = True
    intraday_square_off_hhmm: str = "15:15"
    daily_summary_hhmm: str = "15:30"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "starting_balance_inr": str(self.starting_balance_inr.quantize(Decimal("0.01"))),
            "per_trade_amount_inr": str(self.per_trade_amount_inr.quantize(Decimal("0.01"))),
            "max_open_positions": self.max_open_positions,
            "daily_loss_limit_pct": str(self.daily_loss_limit_pct.quantize(Decimal("0.01"))),
            "max_consecutive_losses": self.max_consecutive_losses,
            "enable_engine_1_orb": self.enable_engine_1_orb,
            "enable_engine_2a_vcp": self.enable_engine_2a_vcp,
            "enable_engine_2b_rsi2": self.enable_engine_2b_rsi2,
            "enable_news_shield": self.enable_news_shield,
            "intraday_square_off_hhmm": self.intraday_square_off_hhmm,
            "daily_summary_hhmm": self.daily_summary_hhmm,
        }


class AutoPaperTrader:
    def __init__(self, config: Optional[AutoTraderConfig] = None) -> None:
        self.config = config or AutoTraderConfig()
        self.kill_switch_active: bool = False
        self.kill_switch_reason: Optional[str] = None
        self.consecutive_losses: int = 0
        self.last_regime: Optional[RegimeAssessment] = None
        self.last_signals: List[Dict[str, Any]] = []
        self.execution_log: List[Dict[str, Any]] = []
        self.last_cycle_ist: Optional[str] = None
        self.last_daily_summary_date: Optional[str] = None
        self.last_scan_stats: Dict[str, Any] = {
            'scanned_symbols': 0,
            'evaluated_count': 0,
            'confirmed_count': 0,
            'watchlist_count': 0,
            'rejected_count': 0,
        }

    def update_config(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        c = self.config
        if "enabled" in updates and updates["enabled"] is not None:
            c.enabled = bool(updates["enabled"])
        if "starting_balance_inr" in updates and updates["starting_balance_inr"] is not None:
            val = Decimal(str(updates["starting_balance_inr"]))
            if val < Decimal("5000.00") or val > Decimal("100000000.00"):
                raise ValueError("starting_balance_inr must be between ₹5,000 and ₹10,00,00,000")
            c.starting_balance_inr = val.quantize(Decimal("0.01"))
        if "per_trade_amount_inr" in updates and updates["per_trade_amount_inr"] is not None:
            val = Decimal(str(updates["per_trade_amount_inr"]))
            if val < Decimal("1000.00") or val > c.starting_balance_inr:
                raise ValueError("per_trade_amount_inr must be >= ₹1,000 and <= starting_balance_inr")
            c.per_trade_amount_inr = val.quantize(Decimal("0.01"))
        if "max_open_positions" in updates and updates["max_open_positions"] is not None:
            c.max_open_positions = max(1, min(15, int(updates["max_open_positions"])))
        if "daily_loss_limit_pct" in updates and updates["daily_loss_limit_pct"] is not None:
            c.daily_loss_limit_pct = max(Decimal("0.25"), min(Decimal("10.00"), Decimal(str(updates["daily_loss_limit_pct"]))))
        if "max_consecutive_losses" in updates and updates["max_consecutive_losses"] is not None:
            c.max_consecutive_losses = max(1, min(10, int(updates["max_consecutive_losses"])))
        for flag in ("enable_engine_1_orb", "enable_engine_2a_vcp", "enable_engine_2b_rsi2", "enable_news_shield"):
            if flag in updates and updates[flag] is not None:
                setattr(c, flag, bool(updates[flag]))
        if updates.get("reset_kill_switch"):
            self.kill_switch_active = False
            self.kill_switch_reason = None
            self.consecutive_losses = 0
        return c.to_dict()

    def _log(self, event: str, details: Dict[str, Any]) -> None:
        now_ist = datetime.datetime.now(IST).isoformat()
        self.execution_log.insert(0, {"ts_ist": now_ist, "event": event, "details": details})
        if len(self.execution_log) > 120:
            self.execution_log = self.execution_log[:120]

    def _charge_buffered_breakeven(self, pos: PaperPosition) -> Decimal:
        """
        Computes the exact breakeven price that covers entry + exit charges + 1 tick,
        so a Breakeven stop NEVER closes at a small negative net P&L.
        """
        profile = CostProfile.for_mode(pos.trading_mode)
        qty_dec = Decimal(str(pos.quantity))
        est_exit_ch = compute_leg_charges(pos.entry_fill_price * qty_dec, is_buy=(pos.direction == "SHORT"), profile=profile)
        slip_rate = profile.slippage_bps / Decimal("10000")
        est_exit_slip_ps = (pos.entry_fill_price * slip_rate).quantize(Decimal("0.05")) + Decimal("0.10")
        per_share_buffer = ((pos.entry_charges_inr + est_exit_ch) / qty_dec) + est_exit_slip_ps + Decimal("0.15")
        if pos.direction == "LONG":
            return round_to_tick(pos.entry_fill_price + per_share_buffer, Decimal("0.05"))
        return round_to_tick(pos.entry_fill_price - per_share_buffer, Decimal("0.05"))

    def monitor_open_positions(
        self,
        user_id: str,
        simulator: PaperTradingSimulator,
        live_ltps: Dict[str, Decimal],
        now_ist: Optional[datetime.datetime] = None,
        telegram_notifier: Optional[Any] = None,
        paper_store: Optional[Any] = None,
    ) -> List[Dict[str, Any]]:
        """
        Checks every OPEN paper position against LIVE LTP:
        1. Target Hit -> Auto Close
        2. Stop-Loss / Trailing SL Hit -> Auto Close
        3. >= 1.5R -> Shift SL to Charge-Buffered Breakeven
        4. >= 2.0R -> Trail SL upward (or downward for SHORT)
        5. >= 15:15 IST -> Auto Square-Off all INTRADAY positions
        """
        dt_ist = now_ist or datetime.datetime.now(IST)
        hhmm = dt_ist.strftime("%H:%M")
        actions: List[Dict[str, Any]] = []

        open_positions = [p for p in simulator.positions.values() if p.user_id == user_id and p.status == "OPEN"]
        for pos in open_positions:
            ltp = live_ltps.get(pos.symbol.upper())
            if ltp is None or ltp <= 0:
                continue

            ref_sl = pos.initial_stop_loss if pos.initial_stop_loss is not None else pos.stop_loss
            init_risk_ps = max(Decimal("0.05"), abs(pos.entry_fill_price - ref_sl))
            live_r = (
                (ltp - pos.entry_fill_price) / init_risk_ps
                if pos.direction == "LONG"
                else (pos.entry_fill_price - ltp) / init_risk_ps
            )

            # 1. Check Target Hit
            target_hit = (ltp >= pos.target_price) if pos.direction == "LONG" else (ltp <= pos.target_price)
            if target_hit:
                closed = simulator.close_paper_position(
                    user_id=user_id, position_id=pos.position_id, planned_exit_price=ltp,
                    exit_reason="TARGET_HIT", now_ist_str=dt_ist.isoformat(),
                )
                if paper_store and getattr(paper_store, "is_configured", lambda: False)():
                    try:
                        paper_store.close_position(closed)
                    except Exception:
                        pass
                self._update_streak_and_kill_switch(user_id, simulator, closed)
                act = {"action": "CLOSED", "reason": "TARGET_HIT", "symbol": pos.symbol, "engine": pos.engine_code, "net_pnl_inr": str(closed.net_pnl_inr), "outcome": closed.outcome}
                actions.append(act)
                self._log("AUTO_CLOSE_TARGET", act)
                if telegram_notifier and getattr(telegram_notifier, "enabled", False):
                    sign = "+" if closed.net_pnl_inr > 0 else ""
                    badge = "✅ WIN" if closed.outcome == "WIN" else ("❌ LOSS" if closed.outcome == "LOSS" else "⚖️ BREAKEVEN")
                    hm = get_horizon_meta(pos.engine_code, pos.trading_mode)
                    telegram_notifier.send_closed_candle_alert(
                        f"🎯 TARGET HIT — {pos.symbol}",
                        (
                            f"⏱ Hold Timeframe: {hm['hold_horizon']}\n"
                            f"⚙️ Engine: {pos.engine_code} ({pos.trading_mode})\n\n"
                            f"💵 Exit Price: ₹{closed.exit_fill_price}  |  Qty: {pos.quantity}\n"
                            f"💰 Net P&L: {sign}₹{closed.net_pnl_inr}  ({badge})"
                        ),
                        dt_ist.strftime("%d %b %Y, %I:%M %p IST"),
                    )
                continue

            # 2. Check Stop-Loss / Trailing SL Hit
            sl_hit = (ltp <= pos.stop_loss) if pos.direction == "LONG" else (ltp >= pos.stop_loss)
            if sl_hit:
                reason = "TRAILING_SL_HIT" if pos.trailing_stop_active else ("BREAKEVEN_SL_HIT" if pos.breakeven_shifted else "STOP_LOSS_HIT")
                closed = simulator.close_paper_position(
                    user_id=user_id, position_id=pos.position_id, planned_exit_price=ltp,
                    exit_reason=reason, now_ist_str=dt_ist.isoformat(),
                )
                if paper_store and getattr(paper_store, "is_configured", lambda: False)():
                    try:
                        paper_store.close_position(closed)
                    except Exception:
                        pass
                self._update_streak_and_kill_switch(user_id, simulator, closed)
                act = {"action": "CLOSED", "reason": reason, "symbol": pos.symbol, "engine": pos.engine_code, "net_pnl_inr": str(closed.net_pnl_inr), "outcome": closed.outcome}
                actions.append(act)
                self._log("AUTO_CLOSE_SL", act)
                if telegram_notifier and getattr(telegram_notifier, "enabled", False):
                    sign = "+" if closed.net_pnl_inr > 0 else ""
                    icon = "🛡️" if "BREAKEVEN" in reason or "TRAILING" in reason else "🛑"
                    badge = "✅ WIN" if closed.outcome == "WIN" else ("❌ LOSS" if closed.outcome == "LOSS" else "⚖️ BREAKEVEN")
                    hm = get_horizon_meta(pos.engine_code, pos.trading_mode)
                    telegram_notifier.send_closed_candle_alert(
                        f"{icon} {reason.replace('_', ' ')} — {pos.symbol}",
                        (
                            f"⏱ Hold Timeframe: {hm['hold_horizon']}\n"
                            f"⚙️ Engine: {pos.engine_code} ({pos.trading_mode})\n\n"
                            f"💵 Exit Price: ₹{closed.exit_fill_price}  |  Qty: {pos.quantity}\n"
                            f"💰 Net P&L: {sign}₹{closed.net_pnl_inr}  ({badge})"
                        ),
                        dt_ist.strftime("%d %b %Y, %I:%M %p IST"),
                    )
                continue

            # 3. Intraday 15:15 IST Auto Square-Off
            if pos.trading_mode == "INTRADAY" and hhmm >= self.config.intraday_square_off_hhmm:
                closed = simulator.close_paper_position(
                    user_id=user_id, position_id=pos.position_id, planned_exit_price=ltp,
                    exit_reason="INTRADAY_SQUARE_OFF", now_ist_str=dt_ist.isoformat(),
                )
                if paper_store and getattr(paper_store, "is_configured", lambda: False)():
                    try:
                        paper_store.close_position(closed)
                    except Exception:
                        pass
                self._update_streak_and_kill_switch(user_id, simulator, closed)
                act = {"action": "CLOSED", "reason": "INTRADAY_SQUARE_OFF", "symbol": pos.symbol, "engine": pos.engine_code, "net_pnl_inr": str(closed.net_pnl_inr), "outcome": closed.outcome}
                actions.append(act)
                self._log("AUTO_SQUARE_OFF", act)
                continue

            # 4. Breakeven Shift at >= 1.5R
            if not pos.breakeven_shifted and live_r >= Decimal("1.50"):
                be_price = self._charge_buffered_breakeven(pos)
                if (pos.direction == "LONG" and be_price > pos.stop_loss) or (pos.direction == "SHORT" and be_price < pos.stop_loss):
                    pos.stop_loss = be_price
                    pos.breakeven_shifted = True
                    act = {"action": "SL_BREAKEVEN", "symbol": pos.symbol, "new_sl": str(be_price), "live_r": str(live_r.quantize(Decimal("0.01")))}
                    actions.append(act)
                    self._log("BREAKEVEN_SHIFT", act)

            # 5. Trailing SL at >= 2.0R (locks at least 1.0R)
            if live_r >= Decimal("2.00"):
                trail_sl = (
                    round_to_tick(ltp - init_risk_ps * Decimal("0.85"), Decimal("0.05"))
                    if pos.direction == "LONG"
                    else round_to_tick(ltp + init_risk_ps * Decimal("0.85"), Decimal("0.05"))
                )
                if (pos.direction == "LONG" and trail_sl > pos.stop_loss) or (pos.direction == "SHORT" and trail_sl < pos.stop_loss):
                    pos.stop_loss = trail_sl
                    pos.trailing_stop_active = True
                    act = {"action": "SL_TRAILED", "symbol": pos.symbol, "new_sl": str(trail_sl), "live_r": str(live_r.quantize(Decimal("0.01")))}
                    actions.append(act)
                    self._log("TRAILING_SL_UPDATE", act)

        return actions

    def _update_streak_and_kill_switch(
        self,
        user_id: str,
        simulator: PaperTradingSimulator,
        closed_pos: PaperPosition,
    ) -> None:
        if closed_pos.net_pnl_inr < Decimal("0.00"):
            self.consecutive_losses += 1
        elif closed_pos.net_pnl_inr > Decimal("0.00"):
            self.consecutive_losses = 0

        if self.consecutive_losses >= self.config.max_consecutive_losses:
            self.kill_switch_active = True
            self.kill_switch_reason = f"Consecutive losses reached {self.consecutive_losses} (limit {self.config.max_consecutive_losses})."
            self._log("KILL_SWITCH_TRIGGERED", {"reason": self.kill_switch_reason})
            return

        # Daily loss limit check
        today_str = datetime.datetime.now(IST).strftime("%Y-%m-%d")
        today_realised = sum(
            (
                p.net_pnl_inr
                for p in simulator.positions.values()
                if p.user_id == user_id and p.status == "CLOSED" and (p.closed_at_ist or "").startswith(today_str)
            ),
            Decimal("0.00"),
        )
        max_loss_inr = (self.config.starting_balance_inr * self.config.daily_loss_limit_pct / Decimal("100")).quantize(Decimal("0.01"))
        if today_realised <= -max_loss_inr:
            self.kill_switch_active = True
            self.kill_switch_reason = f"Daily loss limit hit: ₹{today_realised} <= -₹{max_loss_inr} ({self.config.daily_loss_limit_pct}%)."
            self._log("KILL_SWITCH_TRIGGERED", {"reason": self.kill_switch_reason})

    def run_auto_scan_and_execute(
        self,
        user_id: str,
        simulator: PaperTradingSimulator,
        symbols: List[str],
        candles_15m_by_sym: Dict[str, List[DomainCandle]],
        candles_1d_by_sym: Dict[str, List[DomainCandle]],
        depth_by_sym: Dict[str, Dict[str, Any]],
        indices_snapshot: List[Dict[str, Any]],
        advances: int,
        declines: int,
        live_ltps: Dict[str, Decimal],
        news_risk_by_sym: Optional[Dict[str, str]] = None,
        telegram_notifier: Optional[Any] = None,
        now_ist: Optional[datetime.datetime] = None,
        execute_entries: bool = True,
        paper_store: Optional[Any] = None,
    ) -> Dict[str, Any]:
        dt_ist = now_ist or datetime.datetime.now(IST)
        self.last_cycle_ist = dt_ist.isoformat()

        # First monitor & manage existing open positions on live LTP
        monitor_actions = self.monitor_open_positions(
            user_id=user_id,
            simulator=simulator,
            live_ltps=live_ltps,
            now_ist=dt_ist,
            telegram_notifier=telegram_notifier,
            paper_store=paper_store,
        )

        # Engine 0: Market Regime Gate
        regime = evaluate_market_regime(indices_snapshot, advances=advances, declines=declines)
        self.last_regime = regime

        # Evaluate all enabled engines across live symbols
        signals: List[EngineSignal] = []
        for sym in symbols:
            s_up = sym.upper()
            c15 = candles_15m_by_sym.get(s_up, [])
            c1d = candles_1d_by_sym.get(s_up, [])
            ob = compute_order_book_imbalance(s_up, depth=depth_by_sym.get(s_up), candles=c15 or c1d)
            if self.config.enable_engine_1_orb and c15:
                signals.append(evaluate_engine_1_orb_vwap(s_up, c15, ob))
            if self.config.enable_engine_2a_vcp and c1d:
                signals.append(evaluate_engine_2a_minervini_vcp(s_up, c1d, ob))
            if self.config.enable_engine_2b_rsi2 and c1d:
                signals.append(evaluate_engine_2b_connors_rsi2(s_up, c1d, ob))

        signals.sort(key=lambda s: (1 if s.status == "CONFIRMED" else 0, s.score, s.expected_net_rr), reverse=True)
        self.last_signals = [self._ser_signal(s) for s in signals[:50]]
        self.last_scan_stats = {
            "scanned_symbols": len(symbols),
            "evaluated_count": len(signals),
            "confirmed_count": sum(1 for s in signals if s.status == "CONFIRMED"),
            "watchlist_count": sum(1 for s in signals if s.status == "WATCHLIST"),
            "rejected_count": sum(1 for s in signals if s.status == "REJECTED"),
        }

        opened_trades: List[Dict[str, Any]] = []
        blocked_reasons: List[str] = []

        if not self.config.enabled or not execute_entries:
            blocked_reasons.append("AUTO_TRADER_PAUSED_OR_SCAN_ONLY")
        elif self.kill_switch_active:
            blocked_reasons.append(f"KILL_SWITCH_ACTIVE: {self.kill_switch_reason}")
        elif not regime.allow_auto_trading:
            blocked_reasons.append(f"REGIME_GATE_BLOCKED: {regime.regime}")
        else:
            ledger = simulator.account_ledger(user_id, self.config.starting_balance_inr, live_ltps)
            available_cash = Decimal(ledger["available_cash_inr"])
            open_symbols = {p.symbol for p in simulator.positions.values() if p.user_id == user_id and p.status == "OPEN"}
            open_count = len(open_symbols)
            news_map = {k.upper(): v.upper() for k, v in (news_risk_by_sym or {}).items()}

            for sig in signals:
                if open_count >= self.config.max_open_positions:
                    break
                if sig.status != "CONFIRMED":
                    continue
                if sig.symbol in open_symbols:
                    continue
                # Gemini News Sentiment Shield
                if self.config.enable_news_shield and sig.direction == "LONG":
                    sent = news_map.get(sig.symbol, "NEUTRAL")
                    if sent in ("BEARISH", "HIGH_RISK"):
                        blocked_reasons.append(f"{sig.symbol}: Blocked by Gemini News Shield ({sent})")
                        continue
                # Size strictly from user's configured per_trade_amount_inr & available cash
                alloc = min(self.config.per_trade_amount_inr, available_cash)
                if sig.entry_price <= 0 or alloc < sig.entry_price:
                    continue
                qty = int(alloc // sig.entry_price)
                if qty < 1:
                    continue
                pos = simulator.open_paper_position(
                    user_id=user_id,
                    symbol=sig.symbol,
                    trading_mode=sig.trading_mode,
                    direction=sig.direction,
                    quantity=qty,
                    planned_entry=sig.entry_price,
                    stop_loss=sig.stop_loss,
                    target_price=sig.target_price,
                    now_ist_str=dt_ist.isoformat(),
                    engine_code=sig.engine_code,
                    rationale=sig.rationale,
                )
                available_cash -= (pos.entry_fill_price * Decimal(str(qty))) + pos.entry_charges_inr
                open_symbols.add(sig.symbol)
                open_count += 1
                hm = get_horizon_meta(pos.engine_code, pos.trading_mode)
                opened_info = {
                    "position_id": pos.position_id,
                    "symbol": pos.symbol,
                    "engine_code": pos.engine_code,
                    "horizon_bucket": hm["horizon_bucket"],
                    "hold_horizon": hm["hold_horizon"],
                    "direction": pos.direction,
                    "quantity": pos.quantity,
                    "entry_fill_price": str(pos.entry_fill_price),
                    "stop_loss": str(pos.stop_loss),
                    "target_price": str(pos.target_price),
                    "entry_charges_inr": str(pos.entry_charges_inr),
                }
                opened_trades.append(opened_info)
                if paper_store and getattr(paper_store, "is_configured", lambda: False)():
                    try:
                        paper_store.insert_position(pos)
                    except Exception:
                        pass
                self._log("AUTO_OPEN_TRADE", opened_info)
                if telegram_notifier and getattr(telegram_notifier, "enabled", False):
                    dir_icon = "🟢 AUTO BUY (LONG)" if pos.direction == "LONG" else "🔴 AUTO SELL (SHORT)"
                    telegram_notifier.send_closed_candle_alert(
                        f"{dir_icon} — {pos.symbol}",
                        (
                            f"📌 Type: {hm['horizon_label'].upper()} ({pos.direction})\n"
                            f"⏱ Hold Timeframe: {hm['hold_horizon']}\n"
                            f"⚙️ Strategy: {pos.engine_code}\n\n"
                            f"💰 Entry: ₹{pos.entry_fill_price}  |  Qty: {pos.quantity}\n"
                            f"🛡️ Stop-Loss: ₹{pos.stop_loss}\n"
                            f"🎯 Target: ₹{pos.target_price}\n\n"
                            f"📊 Why: {pos.rationale[:160]}"
                        ),
                        dt_ist.strftime("%d %b %Y, %I:%M %p IST"),
                    )

        return {
            "cycle_ist": self.last_cycle_ist,
            "regime": {
                "regime": regime.regime,
                "allow_auto_trading": regime.allow_auto_trading,
                "recommended_engines": regime.recommended_engines,
                "breadth_pct": str(regime.breadth_pct),
                "avg_atr_pct": str(regime.avg_atr_pct),
                "trend_score": regime.trend_score,
                "summary": regime.summary,
            },
            "kill_switch_active": self.kill_switch_active,
            "kill_switch_reason": self.kill_switch_reason,
            "monitor_actions": monitor_actions,
            "opened_trades": opened_trades,
            "blocked_reasons": blocked_reasons,
            "signals_evaluated": len(signals),
            "confirmed_signals": sum(1 for s in signals if s.status == "CONFIRMED"),
            "top_signals": self.last_signals[:15],
        }

    def build_and_send_daily_summary(
        self,
        user_id: str,
        simulator: PaperTradingSimulator,
        live_ltps: Dict[str, Decimal],
        telegram_notifier: Optional[Any] = None,
        send_telegram: bool = False,
        ai_review: Optional[str] = None,
    ) -> Dict[str, Any]:
        dt_ist = datetime.datetime.now(IST)
        today_str = dt_ist.strftime("%Y-%m-%d")
        ledger = simulator.account_ledger(user_id, self.config.starting_balance_inr, live_ltps)
        closed_today = [
            p for p in simulator.positions.values()
            if p.user_id == user_id and p.status == "CLOSED" and (p.closed_at_ist or "").startswith(today_str)
        ]
        targets_hit = sum(1 for p in closed_today if p.exit_reason == "TARGET_HIT")
        sls_hit = sum(1 for p in closed_today if "SL" in (p.exit_reason or ""))
        today_net = sum((p.net_pnl_inr for p in closed_today), Decimal("0.00")).quantize(Decimal("0.01"))

        t_sign = "+" if today_net > 0 else ""
        u_val = Decimal(str(ledger["unrealised_net_pnl_inr"]))
        u_sign = "+" if u_val > 0 else ""
        lines = [
            f"📊 DAILY PAPER SUMMARY — {today_str}",
            "",
            f"💼 Live Equity: ₹{ledger['total_equity_inr']}",
            f"💵 Available Cash: ₹{ledger['available_cash_inr']}",
            "",
            f"📈 Closed Today: {len(closed_today)}  (🎯 Targets: {targets_hit}  |  🛑 SLs: {sls_hit})",
            f"💰 Today Net P&L: {t_sign}₹{today_net}",
            "",
            f"🏆 Accuracy: {ledger['accuracy_pct']}% ({ledger['wins']}W / {ledger['losses']}L)  |  PF: {ledger['profit_factor']}",
            f"📂 Open Positions: {ledger['open_count']}  |  Unrealised: {u_sign}₹{ledger['unrealised_net_pnl_inr']}",
        ]
        if ai_review:
            lines.extend(["", f"🤖 AI Review: {ai_review[:240]}"])
        message = "\n".join(lines)

        tg_result = {"delivered": False, "reason": "SEND_NOT_REQUESTED"}
        if send_telegram and telegram_notifier:
            tg_result = telegram_notifier.send_closed_candle_alert(
                "DAILY SUMMARY",
                message,
                dt_ist.strftime("%Y-%m-%d %H:%M IST"),
            )
            self.last_daily_summary_date = today_str

        return {
            "date_ist": today_str,
            "summary_text": message,
            "closed_today": len(closed_today),
            "targets_hit_today": targets_hit,
            "sls_hit_today": sls_hit,
            "today_net_pnl_inr": str(today_net),
            "ledger": ledger,
            "telegram": tg_result,
        }

    @staticmethod
    def _ser_signal(s: EngineSignal) -> Dict[str, Any]:
        hm = get_horizon_meta(s.engine_code, s.trading_mode)
        return {
            "symbol": s.symbol,
            "engine_code": s.engine_code,
            "engine_name": s.engine_name,
            "trading_mode": s.trading_mode,
            "horizon_bucket": hm["horizon_bucket"],
            "horizon_label": hm["horizon_label"],
            "hold_horizon": hm["hold_horizon"],
            "direction": s.direction,
            "status": s.status,
            "score": s.score,
            "entry_price": str(s.entry_price),
            "stop_loss": str(s.stop_loss),
            "target_price": str(s.target_price),
            "expected_gross_rr": str(s.expected_gross_rr),
            "expected_net_rr": str(s.expected_net_rr),
            "estimated_roundtrip_charges_per_lot": str(s.estimated_roundtrip_charges_per_lot),
            "passed_rules": s.passed_rules,
            "failed_rules": s.failed_rules,
            "order_book_pressure": s.order_book_pressure,
            "rationale": s.rationale,
        }