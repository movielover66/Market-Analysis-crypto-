import unittest
from fastapi.testclient import TestClient

from app.main import create_app
from tests._auth_helpers import owner_headers, owner_settings


class TestApiE2EPhases2To6(unittest.TestCase):
    def setUp(self) -> None:
        settings = owner_settings()
        self.app = create_app(settings)
        # Authenticated as the single owner. Request bodies below still carry a legacy "user_id"
        # field (as the existing frontend sends); the backend must ignore it.
        self.client = TestClient(self.app, headers=owner_headers(settings))

    def test_scanner_and_planner_endpoints(self) -> None:
        r_scan = self.client.get("/api/v1/scanner?trading_mode=SWING&strategy_code=BREAKOUT_RETEST&symbols=RELIANCE,TCS")
        self.assertEqual(r_scan.status_code, 200)
        body = r_scan.json()
        self.assertEqual(body["scanned_count"], 2)
        self.assertEqual(body["freshness"], "SIMULATED")

        r_plan = self.client.post(
            "/api/v1/planner/evaluate",
            json={"symbol": "RELIANCE", "trading_mode": "SWING", "strategy_code": "BREAKOUT_RETEST", "capital_inr": 75000.0},
        )
        self.assertEqual(r_plan.status_code, 200)
        plan_body = r_plan.json()
        self.assertEqual(plan_body["symbol"], "RELIANCE")
        self.assertIn("trade_plan", plan_body)
        self.assertIn("ai_explanation", plan_body)

    def test_paper_trading_journal_and_analytics_flow(self) -> None:
        r_open = self.client.post(
            "/api/v1/paper/positions",
            json={
                "user_id": "demo-user",
                "symbol": "RELIANCE",
                "direction": "LONG",
                "trading_mode": "SWING",
                "strategy_code": "BREAKOUT_RETEST",
                "quantity": 5,
                "entry_price": 1285.50,
                "stop_loss": 1265.00,
                "target_price": 1340.00,
            },
        )
        self.assertEqual(r_open.status_code, 200)
        pos_id = r_open.json()["position"]["position_id"]

        r_close = self.client.post(
            f"/api/v1/paper/positions/{pos_id}/close",
            json={"user_id": "demo-user", "exit_price": 1335.00, "exit_reason": "TARGET_HIT"},
        )
        self.assertEqual(r_close.status_code, 200)
        self.assertEqual(r_close.json()["closed_position"]["status"], "CLOSED")

        r_journal = self.client.get("/api/v1/journal")
        self.assertEqual(r_journal.status_code, 200)
        self.assertEqual(r_journal.json()["count"], 1)

        r_analytics = self.client.get("/api/v1/analytics")
        self.assertEqual(r_analytics.status_code, 200)
        self.assertTrue(r_analytics.json()["has_closed_trades"])
        self.assertEqual(r_analytics.json()["analytics"]["trade_count"], 1)

    def test_backtest_news_alerts_and_grounded_assistant(self) -> None:
        r_bt = self.client.post(
            "/api/v1/backtest/run",
            json={
                "symbol": "RELIANCE",
                "timeframe": "1d",
                "strategy_code": "BREAKOUT_RETEST",
            },
        )
        self.assertEqual(r_bt.status_code, 200)
        self.assertTrue(len(r_bt.json()["backtest"]["data_hash"]) > 0)

        # Seed a deterministic fixture; production endpoint now fetches real feeds and does not invent news.
        self.app.state.news_alerts_service.ingest_news_item(
            headline="Test-only verified fixture headline", source_name="Unit Test",
            source_url="https://example.invalid/test", published_at_ist="2026-11-18T09:00:00+05:30",
            related_symbol="RELIANCE",
        )
        r_news = self.client.get("/api/v1/news?symbol=RELIANCE")
        self.assertEqual(r_news.status_code, 200)
        self.assertEqual(r_news.json()["count"], 1)

        r_alert = self.client.post(
            "/api/v1/alerts",
            json={
                "rule_id": "R1",
                "symbol": "RELIANCE",
                "condition_type": "CROSSES_ABOVE",
                "threshold": 100.0,
            },
        )
        self.assertEqual(r_alert.status_code, 200)
        self.assertEqual(r_alert.json()["alert"]["symbol"], "RELIANCE")
        self.assertTrue(r_alert.json()["alert"]["triggered"])

        # Grounded AI assistant normal query + refusal guardrail for live orders
        r_ai = self.client.post(
            "/api/v1/assistant/query",
            json={"question": "Explain the RELIANCE setup and risk.", "symbol": "RELIANCE", "trading_mode": "SWING"},
        )
        self.assertEqual(r_ai.status_code, 200)
        self.assertTrue(r_ai.json()["validation_passed"])

        r_refuse = self.client.post(
            "/api/v1/assistant/query",
            json={"question": "Place live order with real money now", "symbol": "RELIANCE", "trading_mode": "SWING"},
        )
        self.assertEqual(r_refuse.status_code, 200)
        self.assertEqual(r_refuse.json()["mode"], "REFUSAL_GUARDRAIL")


if __name__ == "__main__":
    unittest.main()
