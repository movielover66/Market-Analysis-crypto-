"""
Milestone 1.1 & 1.2 Acceptance Tests:
- FastAPI app factory & /health + /api/v1/health + /api/v1/system/status
- Environment validation (missing production config fails clearly; wildcard CORS rejected in production)
- Structured JSON logging & secret scrubbing (no secrets in logs or repr)
- Standard error envelope with X-Request-ID correlation
"""
import unittest
from fastapi.testclient import TestClient

from app.core.config import Settings, load_settings_from_env
from app.core.errors import TradeIQException
from app.core.logging import scrub_secrets
from app.main import create_app
from tests._auth_helpers import owner_headers, owner_settings


class TestFoundationMilestone1(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = owner_settings(
            supabase_url="https://example.supabase.co",
            supabase_jwt_secret="super_secret_jwt_key_123",
            angel_api_key="angel_secret_key",
            gemini_api_key="gemini_secret_key",
        )
        self.app = create_app(self.settings)

        @self.app.get("/api/v1/test-domain-error")
        async def _trigger_domain_error():
            raise TradeIQException(
                code="RISK_LIMIT_BREACHED",
                message="Daily loss lock (1.0%) is active.",
                status_code=422,
                details={"daily_loss_pct": 1.0, "angel_api_key": "must_be_redacted"},
            )

        self.client = TestClient(self.app)

    def test_root_and_v1_health_endpoints(self) -> None:
        r1 = self.client.get("/health", headers={"X-Request-ID": "req-health-001"})
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r1.headers.get("X-Request-ID"), "req-health-001")
        body1 = r1.json()
        self.assertEqual(body1["status"], "ok")
        self.assertTrue(body1["demo_mode"])

        r2 = self.client.get("/api/v1/health")
        self.assertEqual(r2.status_code, 200)
        body2 = r2.json()
        self.assertEqual(body2["freshness_banner"], "SIMULATED")
        self.assertIn("X-Request-ID", r2.headers)

    def test_system_status_exposes_no_secrets(self) -> None:
        r = self.client.get("/api/v1/system/status", headers=owner_headers(self.settings))
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["exchange"], "NSE")
        self.assertEqual(data["segment"], "CASH_EQUITY")
        self.assertEqual(data["execution_mode"], "PAPER_ONLY")
        self.assertFalse(data["live_order_execution"])
        raw_text = r.text
        self.assertNotIn("super_secret_jwt_key_123", raw_text)
        self.assertNotIn("angel_secret_key", raw_text)
        self.assertNotIn("gemini_secret_key", raw_text)
        self.assertNotIn("super_secret_jwt_key_123", repr(self.settings))

    def test_standard_error_envelope_and_secret_scrubbing(self) -> None:
        r = self.client.get("/api/v1/test-domain-error", headers={"X-Request-ID": "req-err-999"})
        self.assertEqual(r.status_code, 422)
        err = r.json()["error"]
        self.assertEqual(err["code"], "RISK_LIMIT_BREACHED")
        self.assertEqual(err["request_id"], "req-err-999")
        self.assertEqual(err["details"]["angel_api_key"], "[REDACTED]")

    def test_404_uses_standard_error_envelope(self) -> None:
        r = self.client.get("/api/v1/non-existent-route")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json()["error"]["code"], "NOT_FOUND")

    def test_production_config_validation_fails_clearly(self) -> None:
        # 1. Wildcard CORS in production must fail
        with self.assertRaises(ValueError) as ctx1:
            load_settings_from_env({"APP_ENV": "production", "ALLOWED_ORIGINS": "*"})
        self.assertIn("Wildcard '*'", str(ctx1.exception))

        # 2. Missing Supabase config in production must fail
        with self.assertRaises(ValueError) as ctx2:
            load_settings_from_env({"APP_ENV": "production", "ALLOWED_ORIGINS": "https://app.example.com"})
        self.assertIn("SUPABASE_URL", str(ctx2.exception))

        # 3. DEMO_MODE=false in production without Angel One credentials must fail (no silent fallback)
        with self.assertRaises(ValueError) as ctx3:
            load_settings_from_env({
                "APP_ENV": "production",
                "DEMO_MODE": "false",
                "ALLOWED_ORIGINS": "https://app.example.com",
                "SUPABASE_URL": "https://x.supabase.co",
                "SUPABASE_JWT_SECRET": "jwt-sec",
            })
        self.assertIn("DEMO_MODE is false", str(ctx3.exception))

    def test_scrub_secrets_helper(self) -> None:
        dirty = {
            "user": "pritam",
            "angel_totp_secret": "JBSWY3DPEHPK3PXP",
            "nested": {"Authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig"},
            "msg": "Header Authorization: Bearer abc.def.ghi failed",
        }
        cleaned = scrub_secrets(dirty)
        self.assertEqual(cleaned["angel_totp_secret"], "[REDACTED]")
        self.assertEqual(cleaned["nested"]["Authorization"], "[REDACTED]")
        self.assertIn("Bearer [REDACTED]", cleaned["msg"])


if __name__ == "__main__":
    unittest.main()
