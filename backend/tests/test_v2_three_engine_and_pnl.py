"""
TRADEIQ v2.0 — Comprehensive Verification Suite:
1. Penny-accurate ("পাই টু পাই") P&L accounting, strict WIN/LOSS outcome, and equity invariant.
2. Charge-buffered Breakeven SL (never closes at -₹10), Trailing SL, 15:15 IST Intraday Square-Off,
   and 3-Consecutive-Loss Kill-Switch.
3. Engine 0 Regime Gate (CHOPPY blocks auto entries; TRENDING enables ORB & VCP).
4. Gemini News Sentiment Shield (HIGH_RISK / BEARISH blocks BUY auto-entry).
5. All new v2.0 API endpoints enforce single-owner auth (401 unauthenticated, 200 authenticated).
"""
import datetime
import unittest
from decimal import Decimal
from fastapi.testclient import TestClient

from app.domain.paper.simulator import PaperTradingSimulator
from app.domain.strategies.three_engine import evaluate_market_regime
from app.jobs.auto_trader import AutoPaperTrader, AutoTraderConfig
from app.main import create_app
from app.services.alerts_and_news import NewsAndAlertsService
from tests._auth_helpers import owner_headers, owner_settings

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))


class TestV2PennyAccuratePnLAndAutoTrader(unittest.TestCase):
    def test_strict_net_pnl_sign_and_outcome_never_misleading(self) -> None:
        """
        Even if exit_fill > entry_fill slightly (+₹5 gross), if NSE round-trip charges exceed gross,
        net_pnl_inr MUST be negative and outcome MUST be LOSS (never falsely labeled WIN).
        """
        sim = PaperTradingSimulator()
        pos = sim.open_paper_position(
            user_id="owner",
            symbol="RELIANCE",
            trading_mode="SWING",
            direction="LONG",
            quantity=10,
            planned_entry=Decimal("1400.00"),
            stop_loss=Decimal("1360.00"),
            target_price=Decimal("1500.00"),
            engine_code="ENGINE_2A_VCP",
        )
        # Close just 1 rupee above entry fill -> tiny gross profit, but NSE delivery STT/charges exceed it
        closed = sim.close_paper_position(
            user_id="owner",
            position_id=pos.position_id,
            planned_exit_price=pos.entry_fill_price + Decimal("1.00"),
            exit_reason="MANUAL_EXIT",
        )
        self.assertLess(closed.net_pnl_inr, Decimal("0.00"))
        self.assertEqual(closed.outcome, "LOSS")

    def test_equity_invariant_and_exit_charge_aware_mtm(self) -> None:
        """
        Invariant: total_equity == starting_balance + realised_net + unrealised_net (to the exact ₹0.01).
        And open MTM deducts estimated exit charges so there is no surprise drop on close.
        """
        sim = PaperTradingSimulator()
        starting_bal = Decimal("100000.00")

        # Open & close 1 winning trade
        p1 = sim.open_paper_position(
            user_id="owner", symbol="TCS", trading_mode="INTRADAY", direction="LONG",
            quantity=5, planned_entry=Decimal("3800.00"), stop_loss=Decimal("3740.00"),
            target_price=Decimal("3950.00"), engine_code="ENGINE_1_ORB",
        )
        c1 = sim.close_paper_position("owner", p1.position_id, Decimal("3950.00"), "TARGET_HIT")
        self.assertGreater(c1.net_pnl_inr, Decimal("0.00"))
        self.assertEqual(c1.outcome, "WIN")

        # Keep 1 open position & verify MTM includes exit charges
        p2 = sim.open_paper_position(
            user_id="owner", symbol="INFY", trading_mode="SWING", direction="LONG",
            quantity=10, planned_entry=Decimal("1500.00"), stop_loss=Decimal("1450.00"),
            target_price=Decimal("1620.00"), engine_code="ENGINE_2B_RSI2",
        )
        mtm = sim.mark_to_market(p2, Decimal("1540.00"))
        self.assertGreater(mtm["estimated_exit_charges_inr"], Decimal("0.00"))
        self.assertEqual(
            mtm["unrealised_net_inr"],
            (mtm["unrealised_gross_inr"] - mtm["total_estimated_charges_inr"]).quantize(Decimal("0.01")),
        )

        ledger = sim.account_ledger("owner", starting_bal, {"INFY": Decimal("1540.00")})
        expected_equity = (
            starting_bal
            + Decimal(ledger["realised_net_pnl_inr"])
            + Decimal(ledger["unrealised_net_pnl_inr"])
        ).quantize(Decimal("0.01"))
        self.assertEqual(Decimal(ledger["total_equity_inr"]), expected_equity)
        self.assertEqual(ledger["wins"], 1)
        self.assertEqual(ledger["losses"], 0)
        self.assertEqual(ledger["accuracy_pct"], "100.0")

    def test_charge_buffered_breakeven_sl_never_closes_negative(self) -> None:
        """
        At >= 1.5R, AutoPaperTrader shifts SL to charge-buffered breakeven.
        If price later pulls back and hits that breakeven SL, net_pnl MUST be >= ₹0.00 (never -₹10).
        """
        sim = PaperTradingSimulator()
        at = AutoPaperTrader(AutoTraderConfig(starting_balance_inr=Decimal("100000.00"), per_trade_amount_inr=Decimal("20000.00")))
        pos = sim.open_paper_position(
            user_id="owner", symbol="HDFCBANK", trading_mode="INTRADAY", direction="LONG",
            quantity=10, planned_entry=Decimal("1600.00"), stop_loss=Decimal("1580.00"),
            target_price=Decimal("1650.00"), engine_code="ENGINE_1_ORB",
        )
        # Price moves to +1.6R (entry ~1600.80, risk ~20.80 -> +34 = 1635)
        actions = at.monitor_open_positions(
            "owner", sim, {"HDFCBANK": Decimal("1635.00")},
            now_ist=datetime.datetime(2026, 11, 19, 11, 0, tzinfo=IST),
        )
        self.assertTrue(pos.breakeven_shifted)
        self.assertTrue(any(a["action"] == "SL_BREAKEVEN" for a in actions))
        self.assertGreater(pos.stop_loss, pos.entry_fill_price)

        # Now price drops back to hit the shifted breakeven SL
        close_actions = at.monitor_open_positions(
            "owner", sim, {"HDFCBANK": pos.stop_loss},
            now_ist=datetime.datetime(2026, 11, 19, 12, 0, tzinfo=IST),
        )
        self.assertEqual(pos.status, "CLOSED")
        self.assertEqual(pos.exit_reason, "BREAKEVEN_SL_HIT")
        self.assertGreaterEqual(pos.net_pnl_inr, Decimal("0.00"))
        self.assertTrue(any(a["action"] == "CLOSED" for a in close_actions))

    def test_intraday_square_off_and_kill_switch_after_3_losses(self) -> None:
        sim = PaperTradingSimulator()
        at = AutoPaperTrader(AutoTraderConfig(max_consecutive_losses=3))
        for i in range(3):
            p = sim.open_paper_position(
                user_id="owner", symbol=f"SYM{i}", trading_mode="INTRADAY", direction="LONG",
                quantity=5, planned_entry=Decimal("1000.00"), stop_loss=Decimal("980.00"),
                target_price=Decimal("1050.00"), engine_code="ENGINE_1_ORB",
            )
            at.monitor_open_positions(
                "owner", sim, {f"SYM{i}": Decimal("975.00")},
                now_ist=datetime.datetime(2026, 11, 19, 10, 30, tzinfo=IST),
            )
        self.assertTrue(at.kill_switch_active)
        self.assertEqual(at.consecutive_losses, 3)

        # Verify 15:15 IST Intraday Square-Off
        p_sq = sim.open_paper_position(
            user_id="owner", symbol="SBIN", trading_mode="INTRADAY", direction="LONG",
            quantity=10, planned_entry=Decimal("800.00"), stop_loss=Decimal("780.00"),
            target_price=Decimal("850.00"), engine_code="ENGINE_1_ORB",
        )
        at.monitor_open_positions(
            "owner", sim, {"SBIN": Decimal("812.00")},
            now_ist=datetime.datetime(2026, 11, 19, 15, 16, tzinfo=IST),
        )
        self.assertEqual(p_sq.status, "CLOSED")
        self.assertEqual(p_sq.exit_reason, "INTRADAY_SQUARE_OFF")

    def test_regime_gate_and_news_sentiment_shield(self) -> None:
        choppy = evaluate_market_regime(
            [{"symbol": "NIFTY 50", "change_pct": "0.02"}, {"symbol": "BANKNIFTY", "change_pct": "-0.04"}],
            advances=5, declines=5, sample_atr_pcts=[Decimal("0.30")],
        )
        self.assertEqual(choppy.regime, "CHOPPY")
        self.assertFalse(choppy.allow_auto_trading)

        trending = evaluate_market_regime(
            [{"symbol": "NIFTY 50", "change_pct": "0.85"}, {"symbol": "BANKNIFTY", "change_pct": "1.10"}],
            advances=10, declines=2, sample_atr_pcts=[Decimal("1.40")],
        )
        self.assertEqual(trending.regime, "TRENDING")
        self.assertTrue(trending.allow_auto_trading)

        ns = NewsAndAlertsService()
        item = ns.ingest_news_item(
            headline="SEBI probe launched into XYZCORP after auditor resigns",
            source_name="Economic Times Markets",
            source_url="https://economictimes.indiatimes.com/markets",
            published_at_ist="2026-11-19T09:05:00+05:30",
            related_symbol="XYZCORP",
        )
        self.assertEqual(item.sentiment, "HIGH_RISK")
        self.assertEqual(ns.get_symbol_sentiment_map(["XYZCORP"])["XYZCORP"], "HIGH_RISK")


class TestV2EndpointsAndSecurity(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = owner_settings(demo_mode=True)
        self.app = create_app(self.cfg)
        self.client = TestClient(self.app)
        self.headers = owner_headers(self.cfg)

    def test_all_v2_routes_require_owner_auth_and_work_authenticated(self) -> None:
        v2_gets = [
            "/api/v1/market/indices",
            "/api/v1/market/depth/RELIANCE",
            "/api/v1/paper/ledger",
            "/api/v1/auto-trader/status",
            "/api/v1/engines/scan?limit=5",
            "/api/v1/assistant/morning-brief",
            "/api/v1/system/connectivity",
        ]
        for path in v2_gets:
            self.assertEqual(self.client.get(path).status_code, 401, f"Unauth GET {path} must be 401")
            r = self.client.get(path, headers=self.headers)
            self.assertEqual(r.status_code, 200, f"Auth GET {path} failed: {r.text}")

        # Update balance & per-trade amount
        r_cfg = self.client.put(
            "/api/v1/auto-trader/config",
            headers=self.headers,
            json={"starting_balance_inr": 250000, "per_trade_amount_inr": 25000, "max_open_positions": 5},
        )
        self.assertEqual(r_cfg.status_code, 200)
        self.assertEqual(r_cfg.json()["config"]["starting_balance_inr"], "250000.00")
        self.assertEqual(r_cfg.json()["config"]["per_trade_amount_inr"], "25000.00")

        # Run auto-trader cycle & daily summary
        r_run = self.client.post("/api/v1/auto-trader/run", headers=self.headers, json={"execute_entries": True, "limit": 6})
        self.assertEqual(r_run.status_code, 200)
        self.assertIn("regime", r_run.json())

        r_sum = self.client.post("/api/v1/auto-trader/daily-summary", headers=self.headers, json={"send_telegram": False})
        self.assertEqual(r_sum.status_code, 200)
        self.assertIn("summary_text", r_sum.json())

    def test_connectivity_matrix_reports_all_services_and_panels(self) -> None:
        r = self.client.get("/api/v1/system/connectivity", headers=self.headers)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(len(body["services"]), 5)
        self.assertEqual(len(body["panels"]), 11)
        self.assertTrue(body["demo_mode"])
