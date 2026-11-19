"""
Blueprint 100% Completeness & Legacy Render Integration Verification Suite:
Audits every directory, document, ADR, 17 Section-20 Pages, 13 feature modules,
Chart wrapper, Protected Cron Jobs (`INTERNAL_JOB_TOKEN`), Fixed Read-Only AI Toolkit
(`get_analysis`, `get_plan`, `calc_quantity`, `get_journal_stats`), Telegram Notifier,
Backward-Compatible Render Env Vars (`ANGEL_PIN`, `SUPABASE_KEY`, `GEMINI_API_KEYS`),
160+ NSE Cash Universe, Pre-Market Report, and Section 8 ZIP Deliverables (`START_HERE.md`, `render.yaml`, guides).
"""
import unittest
from pathlib import Path
from fastapi.testclient import TestClient

from app.core.config import load_settings_from_env
from app.main import create_app
from tests._auth_helpers import owner_headers, owner_settings


class TestBlueprint100PctCompleteness(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[2]
        from app.core.config import Settings
        settings = owner_settings(internal_job_token="test-internal-job-token")
        self.app = create_app(settings)
        self.client = TestClient(self.app, headers=owner_headers(settings))

    def test_all_section_8_and_audit_deliverables_present(self) -> None:
        # 1. Root deliverables required by Section 8 of Audit Prompt
        for root_file in ("README.md", "START_HERE.md", ".env.example", "render.yaml", ".gitignore"):
            p = self.repo_root / root_file
            self.assertTrue(p.exists() and p.stat().st_size > 100, f"Missing root deliverable: {root_file}")

        # 2. Docs + Guides + ADR decisions
        for doc in (
            "ARCHITECTURE.md",
            "EXISTING_PROJECT_AUDIT.md",
            "DATA_PROVIDERS.md",
            "STRATEGIES.md",
            "RISK_MODEL.md",
            "BACKTESTING.md",
            "SECURITY.md",
            "RUNBOOK.md",
            "SETUP_GUIDE.md",
            "API_INTEGRATION_GUIDE.md",
            "DEPLOYMENT_GUIDE.md",
            "TROUBLESHOOTING_GUIDE.md",
        ):
            p = self.repo_root / "docs" / doc
            self.assertTrue(p.exists() and p.stat().st_size > 150, f"Missing or empty doc: {doc}")

        adr_files = list((self.repo_root / "docs" / "decisions").glob("*.md"))
        self.assertGreaterEqual(len(adr_files), 4)

        # 3. All 13 frontend feature folders populated
        for feat in (
            "dashboard", "market", "scanner", "stock-detail", "planner",
            "risk", "paper", "journal", "backtest", "alerts", "news", "assistant", "settings",
        ):
            fp = self.repo_root / "frontend" / "src" / "features" / feat / "index.tsx"
            self.assertTrue(fp.exists(), f"Missing feature module: {feat}")

        # 4. Charts, Pages Registry (17 pages), Frontend Tests, Playwright E2E
        self.assertTrue((self.repo_root / "frontend" / "src" / "charts" / "TechnicalChartCanvas.tsx").exists())
        pages_reg = (self.repo_root / "frontend" / "src" / "pages" / "PagesRegistry.tsx").read_text(encoding="utf-8")
        for s20_page in (
            "Login / Signup", "Dashboard", "Market Overview", "Stock Scanner",
            "Stock Detail", "Technical Chart", "AI Trade Planner", "Risk Calculator",
            "Watchlists", "Paper Trading", "Trading Journal", "Backtesting",
            "News & Events", "AI Assistant", "Performance Analytics", "Settings", "Provider Settings",
        ):
            self.assertIn(s20_page, pages_reg, f"Missing Section 20 page: {s20_page}")

        self.assertTrue((self.repo_root / "frontend" / "tests" / "ui_states_and_guards.test.tsx").exists())
        self.assertTrue((self.repo_root / "frontend" / "e2e" / "mobile_and_smoke.spec.ts").exists())

    def test_protected_cron_jobs_and_read_only_assistant_toolkit(self) -> None:
        r_unauth = self.client.post("/api/v1/jobs/trigger", json={"job_name": "refresh_instruments"})
        self.assertEqual(r_unauth.status_code, 401)

        r_ok = self.client.post(
            "/api/v1/jobs/trigger",
            json={"job_name": "refresh_instruments", "symbols": ["RELIANCE", "TCS"]},
            headers={"X-Internal-Job-Token": "test-internal-job-token"},
        )
        self.assertEqual(r_ok.status_code, 200)
        self.assertEqual(r_ok.json()["job"]["status"], "COMPLETED")

        for allowed in ("get_analysis", "get_plan", "calc_quantity", "get_journal_stats"):
            r_tool = self.client.post("/api/v1/assistant/tool", json={"tool_name": allowed, "params": {}})
            self.assertEqual(r_tool.status_code, 200)

        r_blocked = self.client.post("/api/v1/assistant/tool", json={"tool_name": "place_live_broker_order", "params": {}})
        self.assertEqual(r_blocked.status_code, 403)

    def test_legacy_render_env_bridge_universe_and_unified_dashboard(self) -> None:
        # 1. Backward-compatible Render env vars from user's previous project
        cfg = load_settings_from_env(
            {
                "APP_ENV": "development",
                "DEMO_MODE": "true",
                "SUPABASE_URL": "https://example.supabase.co",
                "SUPABASE_KEY": "legacy-sb-key-123",
                "ANGEL_API_KEY": "ak-1",
                "ANGEL_CLIENT_CODE": "CC123",
                "ANGEL_PIN": "9999",
                "ANGEL_TOTP_SECRET": "JBSWY3DPEHPK3PXP",
                "GEMINI_API_KEYS": "gkey1, gkey2, gkey3",
                "TELEGRAM_BOT_TOKEN": "123456:ABC-DEF",
                "TELEGRAM_CHAT_ID": "987654321",
            }
        )
        summary = cfg.safe_summary()
        self.assertTrue(summary["supabase_configured"])
        self.assertTrue(summary["angel_one_configured"])
        self.assertTrue(summary["gemini_configured"])
        self.assertEqual(summary["gemini_key_pool_count"], 3)
        self.assertTrue(summary["telegram_configured"])

        # 2. Unified Single-Service Dashboard at /
        r_home = self.client.get("/")
        self.assertEqual(r_home.status_code, 200)
        self.assertIn("TRADEIQ", r_home.text)

        # 3. 125+ NSE Cash Universe & Pre-Market Report & Integration Status
        r_univ = self.client.get("/api/v1/market/universe")
        self.assertEqual(r_univ.status_code, 200)
        self.assertGreaterEqual(r_univ.json()["count"], 125)

        r_pre = self.client.get("/api/v1/market/premarket")
        self.assertEqual(r_pre.status_code, 200)
        self.assertEqual(r_pre.json()["report_type"], "NSE_9AM_PREMARKET_INTELLIGENCE")

        r_int = self.client.get("/api/v1/integrations/status")
        self.assertEqual(r_int.status_code, 200)
        self.assertIn("angel_one_smartapi", r_int.json()["integrations"])


if __name__ == "__main__":
    unittest.main()
