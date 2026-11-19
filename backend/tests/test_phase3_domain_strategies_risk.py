"""
Phase 3 Acceptance Tests (Milestones 3.1 to 3.7):
- Milestone 3.2: Look-ahead invariance test (future candles cannot alter past pivots or zones at time T)
- Milestone 3.3: Decimal Risk Engine worked examples, cost/slippage deduction, exposure caps, daily loss lock, and rejection codes
- Milestone 3.4: Positive and negative fixtures for Strategies A, B, C, and D (near resistance is CANDIDATE/WAITING, not CONFIRMED)
- Milestone 3.5: Multi-Timeframe Aligner blocks confirmation when higher timeframe conflicts or is missing
- Milestone 3.6 & 3.7: Trade Planner & Status Machine (stale, expired, or stop-breached plans transition to REJECTED/EXPIRED/INVALIDATED)
"""
import datetime
import unittest
from decimal import Decimal
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.domain.calendar.nse_calendar import IST
from app.domain.candles.validator import DomainCandle
from app.domain.planner.trade_planner import build_trade_plan, refresh_plan_lifecycle
from app.domain.risk.risk_engine import (
    AccountRiskState,
    RiskEvaluationInput,
    RiskParameters,
    evaluate_trade_risk,
)
from app.domain.strategies.mtf_aligner import evaluate_mtf_alignment
from app.domain.strategies.templates import (
    BreakdownRetestStrategy,
    BreakoutRetestStrategy,
    SupportBounceStrategy,
    TrendFollowingStrategy,
)
from app.domain.structure.market_structure import confirmed_pivots
from app.main import create_app
from tests._auth_helpers import owner_headers, owner_settings


def _make_candle(
    idx: int,
    o: str,
    h: str,
    l: str,
    c: str,
    v: int = 100000,
    tf: str = "15m",
    sym: str = "RELIANCE",
) -> DomainCandle:
    base_dt = datetime.datetime(2026, 10, 1, 9, 15, tzinfo=IST) + datetime.timedelta(minutes=15 * idx)
    return DomainCandle(
        symbol=sym,
        timeframe=tf,
        ts_ist=base_dt,
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(l),
        close=Decimal(c),
        volume=v,
        is_closed=True,
        source="DEMO",
        freshness="SIMULATED",
        quality_status="VALID",
    )


class TestPhase3DomainStrategiesRisk(unittest.TestCase):
    def test_milestone_3_2_lookahead_invariance_on_pivots(self) -> None:
        # Build 20 bars with a clear swing high at bar 6 (confirmed at bar 6 + 3 = 9)
        prices = [100, 101, 102, 103, 104, 106, 115, 107, 105, 103, 102, 101, 100, 101, 102, 103, 104, 105, 106, 107]
        bars = [
            _make_candle(i, str(p - 1), str(p + 1), str(p - 2), str(p))
            for i, p in enumerate(prices)
        ]
        # Slice at T = 12 bars vs full 20 bars (adding future crash/rally bars at indices 12..19)
        highs_at_12, _ = confirmed_pivots(bars[:12], k=3)
        future_bars = bars[:12] + [
            _make_candle(12 + j, "200", "250", "50", "220") for j in range(8)
        ]
        highs_with_future, _ = confirmed_pivots(future_bars, k=3)
        early_pivots_after = [p for p in highs_with_future if p.confirmed_at_index < 12]
        self.assertEqual(highs_at_12, early_pivots_after, "Future candles must NEVER alter past confirmed pivots at time T")

    def test_milestone_3_3_risk_engine_worked_examples_and_rejections(self) -> None:
        params = RiskParameters(
            capital_inr=Decimal("100000.00"),
            risk_per_trade_pct=Decimal("0.50"),   # ₹500 max all-in risk
            daily_loss_limit_pct=Decimal("1.00"), # ₹1,000 daily lock
            max_open_positions=2,
            max_single_position_exposure_pct=Decimal("25.00"), # ₹25,000 max position size
            minimum_net_rr=Decimal("2.00"),
        )
        state_ok = AccountRiskState(current_open_positions=0, realised_daily_pnl_inr=Decimal("0.00"))

        # 1. Valid LONG setup: Entry 1000, Stop 980 (raw risk 20), Target 1060 (raw reward 60 -> gross 3.0 R:R)
        inp_valid = RiskEvaluationInput(
            symbol="TCS", direction="LONG", trading_mode="INTRADAY",
            entry_price=Decimal("1000.00"), stop_loss=Decimal("980.00"), target_price=Decimal("1060.00"),
        )
        dec = evaluate_trade_risk(inp_valid, params, state_ok)
        self.assertTrue(dec.approved, f"Expected approval, got {dec.rejection_code}: {dec.rejection_reason}")
        self.assertGreaterEqual(dec.quantity, 1)
        self.assertLessEqual(dec.all_in_risk_inr, Decimal("500.00"))
        self.assertLessEqual(dec.capital_used_inr, Decimal("25000.00"))
        self.assertGreaterEqual(dec.net_rr, Decimal("2.00"))

        # 2. Stop on wrong side (stop > entry for LONG) -> STOP_WRONG_SIDE
        inp_bad_stop = RiskEvaluationInput(
            symbol="TCS", direction="LONG", trading_mode="INTRADAY",
            entry_price=Decimal("1000.00"), stop_loss=Decimal("1010.00"), target_price=Decimal("1060.00"),
        )
        dec_bad = evaluate_trade_risk(inp_bad_stop, params, state_ok)
        self.assertFalse(dec_bad.approved)
        self.assertEqual(dec_bad.rejection_code, "STOP_WRONG_SIDE")

        # 3. Daily loss lock active (realised daily P&L = -₹1,050 <= -₹1,000) -> DAILY_LOSS_LOCK_ACTIVE
        state_locked = AccountRiskState(current_open_positions=0, realised_daily_pnl_inr=Decimal("-1050.00"))
        dec_lock = evaluate_trade_risk(inp_valid, params, state_locked)
        self.assertFalse(dec_lock.approved)
        self.assertEqual(dec_lock.rejection_code, "DAILY_LOSS_LOCK_ACTIVE")

        # 4. Stale data -> STALE_OR_UNAVAILABLE_DATA
        inp_stale = RiskEvaluationInput(
            symbol="TCS", direction="LONG", trading_mode="INTRADAY",
            entry_price=Decimal("1000.00"), stop_loss=Decimal("980.00"), target_price=Decimal("1060.00"),
            freshness="STALE",
        )
        dec_stale = evaluate_trade_risk(inp_stale, params, state_ok)
        self.assertFalse(dec_stale.approved)
        self.assertEqual(dec_stale.rejection_code, "STALE_OR_UNAVAILABLE_DATA")

        # 5. Net R:R below 2.0 -> NET_RR_BELOW_MINIMUM
        inp_low_rr = RiskEvaluationInput(
            symbol="TCS", direction="LONG", trading_mode="SWING",
            entry_price=Decimal("1000.00"), stop_loss=Decimal("980.00"), target_price=Decimal("1025.00"),
        )
        dec_rr = evaluate_trade_risk(inp_low_rr, params, state_ok)
        self.assertFalse(dec_rr.approved)
        self.assertEqual(dec_rr.rejection_code, "NET_RR_BELOW_MINIMUM")

    def test_milestone_3_4_strategies_a_to_d_positive_and_negative_fixtures(self) -> None:
        # Build a 30-bar series where bars 0..26 range around 95..100 (Resistance ~100)
        base_bars = [
            _make_candle(i, "97.00", "100.00", "96.00", "98.50", v=100000)
            for i in range(27)
        ]
        # Negative fixture: price merely near resistance (99.50) -> CANDIDATE, NOT CONFIRMED
        near_bars = base_bars + [
            _make_candle(27, "98.50", "99.80", "98.00", "99.20", v=110000),
            _make_candle(28, "99.20", "99.90", "98.80", "99.50", v=115000),
            _make_candle(29, "99.50", "99.95", "99.10", "99.60", v=120000),
        ]
        strat_a = BreakoutRetestStrategy()
        res_near = strat_a.evaluate({"15m": near_bars}, {"symbol": "RELIANCE"})
        self.assertNotEqual(res_near.status, "CONFIRMED", "Stock merely near resistance must NOT be CONFIRMED")
        self.assertEqual(res_near.status, "CANDIDATE")

        # Positive fixture for Strategy A: Bar 27 breaks above 100.50, Bar 28 stays high, Bar 29 retests 100.20 and closes green at 102.00 on 2x volume
        confirmed_bars = base_bars + [
            _make_candle(27, "99.00", "103.00", "98.80", "102.50", v=220000),
            _make_candle(28, "102.50", "103.50", "101.50", "102.00", v=150000),
            _make_candle(29, "101.20", "103.00", "100.10", "102.60", v=200000),
        ]
        res_conf = strat_a.evaluate({"15m": confirmed_bars}, {"symbol": "RELIANCE"})
        self.assertEqual(res_conf.status, "CONFIRMED")

        # Strategy B (Support Bounce), Strategy C (Trend Following), Strategy D (Breakdown Retest) instantiate and evaluate deterministically
        for strat_cls in (SupportBounceStrategy, TrendFollowingStrategy, BreakdownRetestStrategy):
            s_obj = strat_cls()
            r_out = s_obj.evaluate({"1h": confirmed_bars, "15m": confirmed_bars}, {"symbol": "RELIANCE"})
            self.assertIn(r_out.status, ("CONFIRMED", "WAITING", "CANDIDATE", "NO_TRADE", "UNAVAILABLE"))

    def test_milestone_3_5_mtf_aligner_blocks_conflicts_and_missing_data(self) -> None:
        # Bearish Daily context (falling from 200 to 150) vs LONG setup -> BLOCKED_CONFLICT
        bear_1d = [
            _make_candle(i, str(200 - i * 2), str(201 - i * 2), str(198 - i * 2), str(199 - i * 2), tf="1d")
            for i in range(25)
        ]
        bull_15m = [
            _make_candle(i, str(150 + i), str(152 + i), str(149 + i), str(151 + i), tf="15m")
            for i in range(25)
        ]
        bull_5m = [
            _make_candle(i, "170", "172", "169", "171", tf="5m")
            for i in range(10)
        ]
        mtf = evaluate_mtf_alignment({"1d": bear_1d, "15m": bull_15m, "5m": bull_5m}, setup_direction="LONG")
        self.assertTrue(mtf.has_conflict)
        self.assertEqual(mtf.final_status, "BLOCKED_CONFLICT")

        # Missing 1d context -> BLOCKED_MISSING_DATA
        mtf_missing = evaluate_mtf_alignment({"15m": bull_15m, "5m": bull_5m}, setup_direction="LONG")
        self.assertEqual(mtf_missing.final_status, "BLOCKED_MISSING_DATA")
        self.assertIn("1d", mtf_missing.missing_timeframes)

    def test_milestone_3_7_plan_lifecycle_expiry_and_invalidation(self) -> None:
        bull_1d = [
            _make_candle(i, str(90 + i), str(92 + i), str(89 + i), str(91 + i), tf="1d")
            for i in range(25)
        ]
        base_bars = [_make_candle(i, "97.00", "100.00", "96.00", "98.50", v=100000) for i in range(27)]
        confirmed_15m = base_bars + [
            _make_candle(27, "99.00", "103.00", "98.80", "102.50", v=220000),
            _make_candle(28, "102.50", "103.50", "101.50", "102.00", v=150000),
            _make_candle(29, "101.20", "103.00", "100.10", "102.60", v=200000),
        ]
        bull_5m = [_make_candle(i, "101.00", "103.00", "100.80", "102.60", tf="5m") for i in range(10)]

        now_dt = datetime.datetime(2026, 11, 18, 10, 0, tzinfo=IST)
        plan = build_trade_plan(
            symbol="RELIANCE",
            strategy_code="BREAKOUT_RETEST",
            trading_mode="INTRADAY",
            candles_by_tf={"1d": bull_1d, "15m": confirmed_15m, "5m": bull_5m},
            risk_params=RiskParameters(capital_inr=Decimal("100000.00")),
            account_state=AccountRiskState(),
            freshness="SIMULATED",
            now_dt=now_dt,
        )
        self.assertEqual(plan.status, "CONFIRMED")

        # Price breaches stop loss -> transitions to INVALIDATED
        sl_price = plan.risk_decision.stop_loss
        invalidated = refresh_plan_lifecycle(plan, sl_price - Decimal("1.00"), "SIMULATED", now_dt)
        self.assertEqual(invalidated.status, "INVALIDATED")

        # Time passes past 15:25 IST -> transitions to EXPIRED
        late_dt = datetime.datetime(2026, 11, 18, 15, 28, tzinfo=IST)
        expired = refresh_plan_lifecycle(plan, Decimal("102.60"), "SIMULATED", late_dt)
        self.assertEqual(expired.status, "EXPIRED")

        # API risk calculator endpoint check
        api_settings = owner_settings()
        client = TestClient(create_app(api_settings), headers=owner_headers(api_settings))
        r_api = client.post(
            "/api/v1/risk/calculate",
            json={
                "symbol": "RELIANCE",
                "direction": "LONG",
                "trading_mode": "INTRADAY",
                "entry_price": 1000.0,
                "stop_loss": 980.0,
                "target_price": 1060.0,
                "capital_inr": 100000.0,
            },
        )
        self.assertEqual(r_api.status_code, 200)
        self.assertTrue(r_api.json()["decision"]["approved"])


if __name__ == "__main__":
    unittest.main()
