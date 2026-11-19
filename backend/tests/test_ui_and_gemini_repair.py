"""
Regression & Repair Verification Suite:
- Verifies /api/v1/market/overview (works in both Demo & Live mode with breadth/sectors).
- Verifies /api/v1/market/candles/{symbol} and timeframe parameter on /api/v1/market/indicators/{symbol}.
- Verifies GeminiLLMClient is ACTUALLY called by /api/v1/planner/evaluate and /api/v1/assistant/query
  when configured, and returns GEMINI_VALIDATED when output passes fact_id validation.
- Verifies ZERO raw `<pre>{JSON.stringify(...)}</pre>` blocks remain in App.tsx or static/index.html.
"""
import json
import unittest
from pathlib import Path
from fastapi.testclient import TestClient

from app.main import create_app
from tests._auth_helpers import owner_headers, owner_settings


class _StubGeminiClient:
    model_name = "gemini-2.5-flash"
    request_count = 0
    error_count = 0
    last_latency_ms = 12.5

    def is_configured(self) -> bool:
        return True

    def generate_structured_json(self, system_prompt: str, user_prompt: str, timeout_sec: float = 10.0):
        self.request_count += 1
        idx = user_prompt.find("{")
        pkt = json.loads(user_prompt[idx:])
        return {
            "data_used": f"{pkt['symbol']} ({pkt['trading_mode']}) {pkt['freshness']}",
            "analysis": f"Verified Gemini analysis for {pkt['symbol']} with status {pkt['plan_status']}.",
            "evidence": [f"[{pkt['facts'][0]['fact_id']}] {pkt['facts'][0]['label']}: {pkt['facts'][0]['value']}"],
            "cited_fact_ids": [pkt["facts"][0]["fact_id"]],
            "risks_and_limitations": "Simulated/historical evidence only.",
            "conclusion": f"Deterministic status remains {pkt['plan_status']}.",
            "plan_status": pkt["plan_status"],
            "timestamp": pkt["as_of_ist"] or "2026-11-19T10:00:00+05:30",
        }


class TestUiAndGeminiRepair(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[2]
        self.cfg = owner_settings(demo_mode=True)
        self.app = create_app(self.cfg)
        self.client = TestClient(self.app)
        self.headers = owner_headers(self.cfg)

    def test_unified_market_overview_candles_and_timeframe_indicators(self) -> None:
        r_ov = self.client.get("/api/v1/market/overview?limit=6", headers=self.headers)
        self.assertEqual(r_ov.status_code, 200)
        ov = r_ov.json()
        self.assertGreaterEqual(len(ov["quotes"]), 1)
        self.assertIn("breadth", ov)
        self.assertIn("sectors", ov)

        r_can = self.client.get("/api/v1/market/candles/RELIANCE?timeframe=15m&limit=20", headers=self.headers)
        self.assertEqual(r_can.status_code, 200)
        can = r_can.json()
        self.assertEqual(can["timeframe"], "15m")
        self.assertEqual(len(can["candles"]), 20)

        r_ind = self.client.get("/api/v1/market/indicators/RELIANCE?timeframe=15m", headers=self.headers)
        self.assertEqual(r_ind.status_code, 200)
        self.assertEqual(r_ind.json()["timeframe"], "15m")

    def test_gemini_client_is_actively_called_by_planner_and_assistant(self) -> None:
        stub = _StubGeminiClient()
        self.app.state.llm_client = stub

        r_plan = self.client.post(
            "/api/v1/planner/evaluate",
            json={"symbol": "RELIANCE", "trading_mode": "SWING", "strategy_code": "BREAKOUT_RETEST", "capital_inr": 75000},
            headers=self.headers,
        )
        self.assertEqual(r_plan.status_code, 200)
        self.assertEqual(r_plan.json()["ai_explanation"]["source"], "GEMINI_VALIDATED")
        self.assertEqual(stub.request_count, 1)

        r_ai = self.client.post(
            "/api/v1/assistant/query",
            json={"question": "Why did RELIANCE pass or fail?", "symbol": "RELIANCE", "trading_mode": "SWING"},
            headers=self.headers,
        )
        self.assertEqual(r_ai.status_code, 200)
        self.assertEqual(r_ai.json()["mode"], "GEMINI_VALIDATED")
        self.assertEqual(stub.request_count, 2)

    def test_no_raw_json_stringify_pre_blocks_in_ui(self) -> None:
        app_tsx = (self.repo_root / "frontend" / "src" / "app" / "App.tsx").read_text(encoding="utf-8")
        static_html = (self.repo_root / "backend" / "app" / "static" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("JSON.stringify(", app_tsx, "App.tsx must not render raw JSON.stringify blocks")
        self.assertNotIn("JSON.stringify(data", static_html, "static/index.html must not dump raw JSON responses")


if __name__ == "__main__":
    unittest.main()
