"""
Phase 1 Milestones 1.3, 1.4, 1.5 & 1.6 Acceptance Tests:
- Milestone 1.3: React TypeScript PWA shell, 5 mobile tabs, zero secrets in frontend source
- Milestone 1.4: PostgreSQL migrations (0001_initial_schema.sql, 0002_rls_policies.sql, seed_demo.sql) & RLS isolation
- Milestone 1.5: JWT Auth (401 on missing/expired/invalid token), User A cannot access User B rows, encrypted provider credentials
- Milestone 1.6: Demo Mode (SIMULATED labels on all demo data; 403 DEMO_MODE_DISABLED when demo_mode=False)
"""
import re
import time
import unittest
from pathlib import Path
from fastapi.testclient import TestClient

from app.core.auth import create_hs256_jwt
from app.core.config import Settings
from app.core.crypto import decrypt_secret_payload, encrypt_secret_payload
from app.main import create_app
from app.repositories.user_repository import UserRepository
from tests._auth_helpers import owner_settings


class TestPhase1AuthRlsDemo(unittest.TestCase):
    def setUp(self) -> None:
        self.jwt_secret = "test_supabase_jwt_secret_32_bytes_minimum_key"
        # Single-owner deployment: User A's Supabase UUID is the configured owner.
        self.settings = owner_settings(
            supabase_url="https://example.supabase.co",
            supabase_jwt_secret=self.jwt_secret,
            owner_user_id="11111111-1111-1111-1111-111111111111",
            token_encryption_key="master_encryption_key_for_test_only_1234",
        )
        self.app = create_app(self.settings)
        self.client = TestClient(self.app)

        now = int(time.time())
        self.token_user_a = create_hs256_jwt(
            {"sub": "11111111-1111-1111-1111-111111111111", "email": "user_a@example.com", "role": "authenticated", "aud": "authenticated", "exp": now + 3600},
            self.jwt_secret,
        )
        self.token_user_b = create_hs256_jwt(
            {"sub": "22222222-2222-2222-2222-222222222222", "email": "user_b@example.com", "role": "authenticated", "aud": "authenticated", "exp": now + 3600},
            self.jwt_secret,
        )
        self.expired_token = create_hs256_jwt(
            {"sub": "11111111-1111-1111-1111-111111111111", "exp": now - 60},
            self.jwt_secret,
        )

    def test_unauthenticated_requests_return_401(self) -> None:
        for path in ("/api/v1/auth/me", "/api/v1/settings", "/api/v1/watchlists"):
            r = self.client.get(path)
            self.assertEqual(r.status_code, 401, f"Expected 401 for {path}")
            self.assertEqual(r.json()["error"]["code"], "UNAUTHORIZED")

        # Expired token must return 401
        r_exp = self.client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {self.expired_token}"})
        self.assertEqual(r_exp.status_code, 401)

        # Tampered signature must return 401
        r_bad = self.client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {self.token_user_a}tampered"})
        self.assertEqual(r_bad.status_code, 401)

    def test_owner_access_and_non_owner_rejection(self) -> None:
        headers_a = {"Authorization": f"Bearer {self.token_user_a}"}
        headers_b = {"Authorization": f"Bearer {self.token_user_b}"}

        # Owner (User A) updates capital to 90,000 INR
        r_upd = self.client.put("/api/v1/settings", json={"capital_inr": 90000.0, "risk_per_trade_pct": 0.5}, headers=headers_a)
        self.assertEqual(r_upd.status_code, 200)
        self.assertEqual(r_upd.json()["settings"]["capital_inr"], "90000.00")

        # A second, validly-signed Supabase user is NOT the owner: single-user deployment => 403 everywhere.
        for method, path in (("get", "/api/v1/settings"), ("get", "/api/v1/watchlists"), ("get", "/api/v1/auth/me")):
            r_b = getattr(self.client, method)(path, headers=headers_b)
            self.assertEqual(r_b.status_code, 403, path)
            self.assertEqual(r_b.json()["error"]["code"], "FORBIDDEN")

        # Owner asking for another user's settings via target_user_id is still refused (identity is never client-chosen).
        r_cross = self.client.get(
            "/api/v1/settings?target_user_id=22222222-2222-2222-2222-222222222222",
            headers=headers_a,
        )
        self.assertEqual(r_cross.status_code, 403)
        self.assertEqual(r_cross.json()["error"]["code"], "FORBIDDEN")

        # Owner creates and reads a watchlist
        r_wl = self.client.post(
            "/api/v1/watchlists",
            json={"name": "Breakout Candidates", "symbols": ["RELIANCE", "TCS"]},
            headers=headers_a,
        )
        self.assertEqual(r_wl.status_code, 201)
        wl_id = r_wl.json()["watchlist"]["id"]
        self.assertEqual(self.client.get(f"/api/v1/watchlists/{wl_id}", headers=headers_a).status_code, 200)
        self.assertEqual(self.client.get(f"/api/v1/watchlists/{wl_id}", headers=headers_b).status_code, 403)

    def test_repository_layer_still_enforces_per_user_isolation(self) -> None:
        """Defence in depth: even below the API layer, rows are scoped to the authenticated id."""
        repo = UserRepository()
        wl = repo.create_watchlist("user-a", "Private", ["TCS"])
        self.assertEqual(repo.list_watchlists("user-b"), [])
        with self.assertRaises(Exception) as ctx:
            repo.get_watchlist("user-b", wl["id"])
        self.assertEqual(getattr(ctx.exception, "status_code", None), 404)
        with self.assertRaises(Exception) as ctx2:
            repo.get_settings("user-b", "user-a")
        self.assertEqual(getattr(ctx2.exception, "status_code", None), 403)

    def test_provider_credential_encryption_at_rest(self) -> None:
        key = "master_encryption_key_for_test_only_1234"
        secret_payload = {"jwt": "eyJ.secret.jwt", "feed_token": "feed_987654", "client_code": "A12345"}
        encrypted = encrypt_secret_payload(secret_payload, key)
        self.assertNotIn("eyJ.secret.jwt", encrypted)
        self.assertNotIn("feed_987654", encrypted)

        decrypted = decrypt_secret_payload(encrypted, key)
        self.assertEqual(decrypted, secret_payload)

        # Wrong key must fail authentication tag check
        with self.assertRaises(ValueError):
            decrypt_secret_payload(encrypted, "wrong_encryption_key_for_test_only_9999")

    def test_demo_mode_simulated_labels_and_live_mode_block(self) -> None:
        # In DEMO_MODE=True, instruments and quotes are explicitly SIMULATED
        auth_a = {"Authorization": f"Bearer {self.token_user_a}"}
        r_inst = self.client.get("/api/v1/demo/instruments", headers=auth_a)
        self.assertEqual(r_inst.status_code, 200)
        body = r_inst.json()
        self.assertTrue(body["demo_mode"])
        self.assertEqual(body["freshness"], "SIMULATED")
        self.assertGreaterEqual(len(body["instruments"]), 6)
        self.assertTrue(all(i["source"] == "DEMO" for i in body["instruments"]))

        r_q = self.client.get("/api/v1/demo/quotes/RELIANCE", headers=auth_a)
        self.assertEqual(r_q.status_code, 200)
        q = r_q.json()["quote"]
        self.assertEqual(q["provenance"]["freshness"], "SIMULATED")
        self.assertEqual(q["provenance"]["source"], "DEMO")
        self.assertTrue(q["provenance"]["is_simulated"])

        # When DEMO_MODE=False (Live Mode), demo endpoints must refuse to serve simulated data
        live_settings = owner_settings(
            demo_mode=False,
            supabase_url="https://example.supabase.co",
            supabase_jwt_secret=self.jwt_secret,
            owner_user_id="11111111-1111-1111-1111-111111111111",
        )
        live_client = TestClient(create_app(live_settings))
        r_blocked = live_client.get("/api/v1/demo/quotes/RELIANCE", headers=auth_a)
        self.assertEqual(r_blocked.status_code, 403)
        self.assertEqual(r_blocked.json()["error"]["code"], "DEMO_MODE_DISABLED")

    def test_sql_migrations_and_frontend_pwa_files_complete(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        schema_sql = (repo_root / "db" / "migrations" / "0001_initial_schema.sql").read_text(encoding="utf-8")
        rls_sql = (repo_root / "db" / "migrations" / "0002_rls_policies.sql").read_text(encoding="utf-8")

        for tbl in (
            "market_instruments", "historical_prices", "market_snapshots", "technical_indicators",
            "market_news", "exchange_calendar", "trading_strategies", "strategy_parameters",
            "trade_plans", "trade_plan_events", "user_profiles", "user_settings",
            "watchlists", "watchlist_stocks", "price_alerts", "user_notifications",
            "paper_positions", "paper_orders", "trading_journal", "backtest_runs",
            "backtest_trades", "private.provider_credentials", "private.audit_log",
        ):
            self.assertIn(tbl, schema_sql, f"Table {tbl} missing in 0001_initial_schema.sql")

        self.assertEqual(len(re.findall(r"enable row level security", rls_sql, re.I)), 21)

        # Verify frontend PWA files & zero leaked secrets
        fe_dir = repo_root / "frontend"
        self.assertTrue((fe_dir / "public" / "manifest.webmanifest").exists())
        for ts_file in (fe_dir / "src").rglob("*.ts*"):
            txt = ts_file.read_text(encoding="utf-8")
            self.assertNotIn("SUPABASE_SERVICE_ROLE_KEY", txt)
            self.assertNotIn("ANGEL_TOTP_SECRET", txt)
            self.assertNotIn("GEMINI_API_KEY", txt)


if __name__ == "__main__":
    unittest.main()
