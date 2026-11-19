"""
Backend security tests: single-owner authentication, endpoint protection, identity scoping,
login throttling, token attacks, config validation and the paper-only guardrail.

Everything here uses test-only secrets from tests/_auth_helpers.py.
"""
import base64
import json
import re
import time
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.core.auth import (
    LOCAL_AUDIENCE,
    LOCAL_ISSUER,
    LoginThrottle,
    create_hs256_jwt,
)
from app.core.config import Settings, load_settings_from_env
from app.core.password_hash import hash_password, is_valid_hash_format, verify_password
from app.jobs.cron_runner import TelegramNotifier
from app.main import create_app
from app.repositories.legacy_paper_trade_store import LegacyPaperTradeStore
from tests._auth_helpers import (
    AUTH_JWT_SECRET,
    OTHER_UUID,
    OWNER_PASSWORD,
    OWNER_UUID,
    SUPABASE_JWT_SECRET,
    owner_headers,
    owner_settings,
)

# The ONLY routes allowed to be reachable without an owner token. Anything else must require auth.
EXPECTED_PUBLIC_ROUTES = {
    ("GET", "/api/v1/health"),
    ("POST", "/api/v1/auth/login"),
    ("POST", "/api/v1/jobs/trigger"),  # protected by X-Internal-Job-Token instead of an owner session
}


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _api_operations(app):
    """(METHOD, path) for every operation under /api/v1, taken from the public OpenAPI schema
    (robust across FastAPI versions; works even though /openapi.json is not served by default)."""
    ops = []
    for path, item in app.openapi()["paths"].items():
        if path.startswith("/api/v1"):
            for method in item:
                if method.lower() in {"get", "post", "put", "patch", "delete"}:
                    ops.append((method.upper(), path))
    return ops


def _fill_path(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "x", path)


class SecurityTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = owner_settings(internal_job_token="test-internal-job-token")
        self.app = create_app(self.settings)
        self.client = TestClient(self.app)
        self.auth = owner_headers(self.settings)

    def local_token(self, **claim_overrides) -> str:
        now = int(time.time())
        claims = {
            "iss": LOCAL_ISSUER, "aud": LOCAL_AUDIENCE, "sub": OWNER_UUID, "role": "owner",
            "iat": now, "nbf": now, "exp": now + 600,
        }
        claims.update(claim_overrides)
        return create_hs256_jwt({k: v for k, v in claims.items() if v is not None}, AUTH_JWT_SECRET)

    def supabase_token(self, secret: str = SUPABASE_JWT_SECRET, **claim_overrides) -> str:
        claims = {"sub": OWNER_UUID, "role": "authenticated", "aud": "authenticated", "email": "o@example.com",
                  "exp": int(time.time()) + 600}
        claims.update(claim_overrides)
        return create_hs256_jwt({k: v for k, v in claims.items() if v is not None}, secret)

    def bearer(self, token: str) -> dict:
        return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------------------------
class TestEveryPrivateEndpointRequiresAuth(SecurityTestBase):
    def test_public_surface_is_exactly_the_expected_allowlist(self) -> None:
        """Call every operation with NO credentials; whatever is not rejected with 401 is public."""
        not_rejected = set()
        for method, path in _api_operations(self.app):
            r = self.client.request(method, _fill_path(path))
            if r.status_code != 401:
                not_rejected.add((method, path))
        self.assertEqual(
            not_rejected, EXPECTED_PUBLIC_ROUTES,
            "A route became public (or an expected one vanished). Review it, then update the allowlist.")

    def test_all_private_routes_reject_missing_and_garbage_credentials(self) -> None:
        checked = 0
        for method, path in _api_operations(self.app):
            if (method, path) in EXPECTED_PUBLIC_ROUTES:
                continue
            for headers in ({}, {"Authorization": "Bearer not.a.jwt"}, {"Authorization": "Basic dXNlcjpwYXNz"}):
                r = self.client.request(method, _fill_path(path), headers=headers)
                self.assertEqual(r.status_code, 401, f"{method} {path} with {headers} -> {r.status_code}")
                self.assertEqual(r.json()["error"]["code"], "UNAUTHORIZED")
                self.assertEqual(r.headers.get("www-authenticate"), "Bearer")
            checked += 1
        self.assertGreaterEqual(checked, 25, "Route sweep looks suspiciously small")

    def test_named_private_endpoint_groups_are_protected(self) -> None:
        """Explicit list for the groups called out in the requirements (guards against sweep logic bugs)."""
        cases = [
            ("POST", "/api/v1/paper/positions"), ("GET", "/api/v1/paper/summary"),
            ("POST", "/api/v1/paper/positions/abc/close"),
            ("GET", "/api/v1/journal"), ("POST", "/api/v1/journal"),
            ("GET", "/api/v1/analytics"),
            ("POST", "/api/v1/alerts"), ("POST", "/api/v1/backtest/run"),
            ("GET", "/api/v1/integrations/status"), ("POST", "/api/v1/integrations/supabase/verify"),
            ("GET", "/api/v1/notifications/telegram/status"), ("POST", "/api/v1/notifications/telegram/test"),
            ("POST", "/api/v1/assistant/query"), ("POST", "/api/v1/assistant/tool"),
            ("GET", "/api/v1/scanner"), ("POST", "/api/v1/planner/evaluate"), ("POST", "/api/v1/risk/calculate"),
            ("GET", "/api/v1/news"), ("GET", "/api/v1/system/status"), ("GET", "/api/v1/providers/diagnostics"),
            ("GET", "/api/v1/market/status"), ("GET", "/api/v1/market/instruments"),
            ("GET", "/api/v1/market/universe"), ("GET", "/api/v1/market/premarket"),
            ("GET", "/api/v1/market/indicators/RELIANCE"), ("GET", "/api/v1/demo/instruments"),
            ("GET", "/api/v1/settings"), ("GET", "/api/v1/watchlists"), ("GET", "/api/v1/auth/me"),
        ]
        for method, url in cases:
            self.assertEqual(self.client.request(method, url).status_code, 401, f"{method} {url}")

    def test_owner_token_unlocks_private_endpoints(self) -> None:
        for url in ("/api/v1/paper/summary", "/api/v1/journal", "/api/v1/analytics", "/api/v1/system/status",
                    "/api/v1/integrations/status", "/api/v1/market/universe", "/api/v1/auth/me"):
            self.assertEqual(self.client.get(url, headers=self.auth).status_code, 200, url)

    def test_health_is_public_and_minimal(self) -> None:
        for url in ("/health", "/api/v1/health"):
            r = self.client.get(url)
            self.assertEqual(r.status_code, 200)
            self.assertNotIn("app_env", r.json())
            self.assertNotIn("config", r.json())

    def test_api_docs_and_schema_are_not_public_by_default(self) -> None:
        for url in ("/docs", "/redoc", "/openapi.json"):
            self.assertEqual(self.client.get(url).status_code, 404, url)

    def test_api_docs_can_be_enabled_explicitly(self) -> None:
        client = TestClient(create_app(owner_settings(enable_api_docs=True)))
        self.assertEqual(client.get("/openapi.json").status_code, 200)


# ---------------------------------------------------------------------------------------------
class TestTokenAttacks(SecurityTestBase):
    def status(self, token: str) -> int:
        return self.client.get("/api/v1/auth/me", headers=self.bearer(token)).status_code

    def test_valid_local_and_supabase_owner_tokens_work(self) -> None:
        self.assertEqual(self.status(self.local_token()), 200)
        self.assertEqual(self.status(self.supabase_token()), 200)

    def test_expired_and_not_yet_valid_tokens_rejected(self) -> None:
        self.assertEqual(self.status(self.local_token(exp=int(time.time()) - 5)), 401)
        self.assertEqual(self.status(self.local_token(nbf=int(time.time()) + 3600)), 401)
        self.assertEqual(self.status(self.supabase_token(exp=int(time.time()) - 5)), 401)

    def test_missing_exp_rejected(self) -> None:
        self.assertEqual(self.status(self.local_token(exp=None)), 401)

    def test_alg_none_token_rejected(self) -> None:
        header = _b64(json.dumps({"alg": "none", "typ": "JWT"}).encode())
        payload = _b64(json.dumps({"sub": OWNER_UUID, "iss": LOCAL_ISSUER, "aud": LOCAL_AUDIENCE,
                                   "exp": int(time.time()) + 600}).encode())
        self.assertEqual(self.status(f"{header}.{payload}."), 401)

    def test_wrong_algorithm_header_rejected(self) -> None:
        header = _b64(json.dumps({"alg": "HS512", "typ": "JWT"}).encode())
        payload = _b64(json.dumps({"sub": OWNER_UUID, "exp": int(time.time()) + 600}).encode())
        self.assertEqual(self.status(f"{header}.{payload}.AAAA"), 401)

    def test_tampered_signature_and_wrong_secret_rejected(self) -> None:
        self.assertEqual(self.status(self.local_token() + "x"), 401)
        forged = create_hs256_jwt({"iss": LOCAL_ISSUER, "aud": LOCAL_AUDIENCE, "sub": OWNER_UUID,
                                   "exp": int(time.time()) + 600}, "attacker-guessed-secret-0123456789abcdef")
        self.assertEqual(self.status(forged), 401)

    def test_cross_secret_forgery_rejected(self) -> None:
        # Someone holding only the Supabase secret must not be able to mint an owner-session token.
        forged = create_hs256_jwt({"iss": LOCAL_ISSUER, "aud": LOCAL_AUDIENCE, "sub": OWNER_UUID,
                                   "exp": int(time.time()) + 600}, SUPABASE_JWT_SECRET)
        self.assertEqual(self.status(forged), 401)

    def test_wrong_audience_or_issuer_rejected(self) -> None:
        self.assertEqual(self.status(self.local_token(aud="someone-else")), 401)
        self.assertEqual(self.status(self.supabase_token(aud="anon")), 401)
        self.assertEqual(self.status(self.supabase_token(aud=None)), 401)

    def test_validly_signed_token_for_another_identity_is_forbidden(self) -> None:
        r = self.client.get("/api/v1/paper/summary", headers=self.bearer(self.local_token(sub="attacker")))
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["error"]["code"], "FORBIDDEN")
        r2 = self.client.get("/api/v1/paper/summary", headers=self.bearer(self.supabase_token(sub=OTHER_UUID)))
        self.assertEqual(r2.status_code, 403)

    def test_supabase_service_role_and_anon_style_tokens_rejected(self) -> None:
        # service_role / anon keys are JWTs signed with the same secret but are not user sessions.
        self.assertEqual(self.status(self.supabase_token(role="service_role", sub=None)), 401)
        self.assertEqual(self.status(self.supabase_token(role="service_role")), 401)  # even with owner sub
        self.assertEqual(self.status(self.supabase_token(role="anon", sub=None)), 401)

    def test_malformed_claims_return_401_not_500(self) -> None:
        self.assertEqual(self.status(self.local_token(exp="soon")), 401)
        self.assertEqual(self.status(self.local_token(exp=True)), 401)
        self.assertEqual(self.status(self.local_token(sub="")), 401)
        self.assertEqual(self.status(self.local_token(sub=12345)), 401)
        self.assertEqual(self.status(self.local_token(nbf="never")), 401)
        header = _b64(json.dumps({"alg": "HS256"}).encode())
        body = _b64(json.dumps(["not", "a", "dict"]).encode())
        self.assertEqual(self.status(f"{header}.{body}.AAAA"), 401)

    def test_supabase_tokens_refused_when_owner_uuid_not_configured(self) -> None:
        client = TestClient(create_app(owner_settings(owner_user_id="")))
        r = client.get("/api/v1/auth/me", headers=self.bearer(self.supabase_token()))
        self.assertEqual(r.status_code, 401)

    def test_fails_closed_when_no_auth_is_configured(self) -> None:
        bare = TestClient(create_app(Settings(app_env="test", demo_mode=True)))
        for token in (self.local_token(), self.supabase_token(), "x.y.z"):
            self.assertEqual(bare.get("/api/v1/paper/summary", headers=self.bearer(token)).status_code, 401)
        self.assertEqual(bare.post("/api/v1/auth/login", json={"password": "anything-at-all-123"}).status_code, 503)
        self.assertEqual(bare.get("/api/v1/paper/summary").status_code, 401)


# ---------------------------------------------------------------------------------------------
class TestOwnerLogin(SecurityTestBase):
    def login(self, password: str):
        return self.client.post("/api/v1/auth/login", json={"password": password})

    def test_login_success_returns_working_short_lived_token(self) -> None:
        r = self.login(OWNER_PASSWORD)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["token_type"], "bearer")
        self.assertEqual(body["expires_in"], 3600)
        me = self.client.get("/api/v1/auth/me", headers=self.bearer(body["access_token"]))
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json()["user"]["user_id"], OWNER_UUID)
        self.assertEqual(me.json()["user"]["auth_method"], "owner_session")
        self.assertEqual(r.headers.get("cache-control"), "no-store")

    def test_wrong_password_is_generic_401(self) -> None:
        r = self.login("definitely-the-wrong-password")
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.json()["error"]["message"], "Invalid credentials")
        self.assertNotIn("scrypt", r.text)

    def test_login_input_validation(self) -> None:
        self.assertEqual(self.client.post("/api/v1/auth/login", json={}).status_code, 422)
        self.assertEqual(self.client.post("/api/v1/auth/login", json={"password": ""}).status_code, 422)
        self.assertEqual(self.client.post("/api/v1/auth/login", json={"password": "x" * 5000}).status_code, 422)

    def test_repeated_failures_lock_out_even_the_correct_password(self) -> None:
        for _ in range(5):
            self.assertEqual(self.login("wrong-password-attempt").status_code, 401)
        locked = self.login(OWNER_PASSWORD)
        self.assertEqual(locked.status_code, 429)
        self.assertEqual(locked.json()["error"]["code"], "RATE_LIMITED")
        self.assertGreater(int(locked.headers["retry-after"]), 0)

    def test_login_is_not_a_query_string_endpoint(self) -> None:
        self.assertEqual(self.client.get("/api/v1/auth/login?password=x").status_code, 405)

    def test_token_ttl_is_configurable(self) -> None:
        s = owner_settings(access_token_ttl_minutes=15)
        r = TestClient(create_app(s)).post("/api/v1/auth/login", json={"password": OWNER_PASSWORD})
        self.assertEqual(r.json()["expires_in"], 900)


class TestLoginThrottleUnit(unittest.TestCase):
    def test_lockout_expires_and_success_resets(self) -> None:
        now = [1000.0]
        t = LoginThrottle(max_per_key=3, max_global=100, window_sec=60, clock=lambda: now[0])
        for _ in range(3):
            self.assertEqual(t.retry_after("ip"), 0)
            t.record_failure("ip")
        self.assertGreater(t.retry_after("ip"), 0)
        self.assertEqual(t.retry_after("other-ip"), 0)
        now[0] += 61
        self.assertEqual(t.retry_after("ip"), 0)
        t.record_failure("ip")
        t.record_success("ip")
        self.assertEqual(t.retry_after("ip"), 0)

    def test_global_ceiling_applies_across_clients(self) -> None:
        now = [0.0]
        t = LoginThrottle(max_per_key=100, max_global=4, window_sec=60, clock=lambda: now[0])
        for i in range(4):
            t.record_failure(f"ip-{i}")
        self.assertGreater(t.retry_after("brand-new-ip"), 0)

    def test_key_table_is_bounded(self) -> None:
        t = LoginThrottle(max_per_key=5, max_global=10 ** 9, window_sec=900)
        for i in range(LoginThrottle._MAX_KEYS + 500):
            t.record_failure(f"ip-{i}")
        self.assertLessEqual(len(t._per_key), LoginThrottle._MAX_KEYS)


class TestPasswordHashing(unittest.TestCase):
    def test_hash_verify_roundtrip_and_salting(self) -> None:
        h1, h2 = hash_password("a-long-enough-password"), hash_password("a-long-enough-password")
        self.assertNotEqual(h1, h2)
        self.assertTrue(verify_password("a-long-enough-password", h1))
        self.assertFalse(verify_password("a-long-enough-passwore", h1))
        self.assertNotIn("a-long-enough-password", h1)

    def test_weak_and_malformed_inputs(self) -> None:
        with self.assertRaises(ValueError):
            hash_password("short")
        for bad in ("", "plaintext", "scrypt$1$1$1$AA$AA", "scrypt$16384$8$1$!!$!!", None):
            self.assertFalse(verify_password("whatever-password-1", bad))
            self.assertFalse(is_valid_hash_format(bad or ""))


# ---------------------------------------------------------------------------------------------
class TestIdentityIsNeverClientSupplied(SecurityTestBase):
    OPEN = {"symbol": "RELIANCE", "direction": "LONG", "trading_mode": "SWING", "quantity": 5,
            "entry_price": 1285.50, "stop_loss": 1265.00, "target_price": 1340.00}

    def test_client_supplied_user_id_is_ignored_on_open_and_close(self) -> None:
        r = self.client.post("/api/v1/paper/positions", headers=self.auth, json={**self.OPEN, "user_id": "attacker"})
        self.assertEqual(r.status_code, 200)
        pos = r.json()["position"]
        self.assertEqual(pos["user_id"], OWNER_UUID)
        self.assertNotIn("attacker", {p.user_id for p in self.app.state.paper_simulator.positions.values()})

        close = self.client.post(f"/api/v1/paper/positions/{pos['position_id']}/close", headers=self.auth,
                                 json={"user_id": "attacker", "exit_price": 1335.0, "exit_reason": "TARGET_HIT"})
        self.assertEqual(close.status_code, 200)
        self.assertEqual(close.json()["closed_position"]["user_id"], OWNER_UUID)

    def test_other_users_data_is_invisible_and_not_closable(self) -> None:
        sim = self.app.state.paper_simulator
        foreign = sim.open_paper_position(
            user_id="someone-else", symbol="TCS", trading_mode="SWING", direction="LONG", quantity=3,
            planned_entry=Decimal("4000"), stop_loss=Decimal("3900"), target_price=Decimal("4300"))
        sim.close_paper_position("someone-else", foreign.position_id, Decimal("4200"))
        open_foreign = sim.open_paper_position(
            user_id="someone-else", symbol="INFY", trading_mode="SWING", direction="LONG", quantity=2,
            planned_entry=Decimal("1500"), stop_loss=Decimal("1450"), target_price=Decimal("1600"))
        self.app.state.journal_entries.append({"journal_id": "JRN-FOREIGN", "user_id": "someone-else", "symbol": "TCS"})

        summary = self.client.get("/api/v1/paper/summary", headers=self.auth).json()
        self.assertEqual(summary["open_positions_count"], 0)
        self.assertEqual(summary["closed_positions_count"], 0)
        self.assertEqual(summary["realised_pnl_inr"], "0.00")

        analytics = self.client.get("/api/v1/analytics", headers=self.auth).json()
        self.assertFalse(analytics["has_closed_trades"])

        journal = self.client.get("/api/v1/journal", headers=self.auth).json()
        self.assertEqual(journal["count"], 0)

        # Even claiming to be that user in the body cannot close their position.
        r = self.client.post(f"/api/v1/paper/positions/{open_foreign.position_id}/close", headers=self.auth,
                             json={"user_id": "someone-else", "exit_price": 1550.0})
        self.assertEqual(r.status_code, 404)
        self.assertEqual(sim.positions[open_foreign.position_id].status, "OPEN")

    def test_journal_responses_do_not_expose_user_id_and_are_owner_scoped(self) -> None:
        r = self.client.post("/api/v1/journal", headers=self.auth, json={"symbol": "TCS", "notes": "n"})
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("user_id", r.json()["entry"])
        listed = self.client.get("/api/v1/journal", headers=self.auth).json()
        self.assertEqual(listed["count"], 1)
        self.assertNotIn("user_id", listed["entries"][0])
        self.assertEqual(self.app.state.journal_entries[0]["user_id"], OWNER_UUID)

    def test_auto_journal_from_close_is_owner_scoped(self) -> None:
        r = self.client.post("/api/v1/paper/positions", headers=self.auth, json=self.OPEN).json()
        self.client.post(f"/api/v1/paper/positions/{r['position']['position_id']}/close", headers=self.auth,
                         json={"exit_price": 1335.0})
        self.assertEqual(self.client.get("/api/v1/journal", headers=self.auth).json()["count"], 1)
        self.assertEqual(self.app.state.journal_entries[0]["user_id"], OWNER_UUID)

    def test_assistant_journal_tool_counts_only_owner_entries(self) -> None:
        self.app.state.journal_entries.append({"journal_id": "JRN-F", "user_id": "someone-else"})
        r = self.client.post("/api/v1/assistant/tool", headers=self.auth,
                             json={"tool_name": "get_journal_stats", "params": {}})
        self.assertEqual(r.json()["result"]["journal_count"], 0)

    def test_legacy_ledger_restore_maps_rows_to_owner_identity(self) -> None:
        settings = owner_settings(supabase_url="https://example.supabase.co", supabase_service_role_key="svc-test-key")
        row = {"id": "legacy-1", "symbol": "RELIANCE", "buy_price_inr": 1000, "budget_inr": 5000,
               "status": "HOLDING", "created_at": 1_790_000_000, "stop_loss_inr": 950, "target_inr": 1100,
               "hold_label": "SWING"}
        with patch.object(LegacyPaperTradeStore, "fetch_nse_positions", return_value=[row]):
            with TestClient(create_app(settings)) as client:
                summary = client.get("/api/v1/paper/summary", headers=owner_headers(settings)).json()
        self.assertEqual(summary["open_positions_count"], 1)
        self.assertEqual(summary["open_positions"][0]["position_id"], "legacy-1")
        self.assertEqual(summary["open_positions"][0]["user_id"], OWNER_UUID)


# ---------------------------------------------------------------------------------------------
class TestInternalJobToken(SecurityTestBase):
    BODY = {"job_name": "refresh_instruments"}

    def test_job_requires_machine_token_not_owner_session(self) -> None:
        self.assertEqual(self.client.post("/api/v1/jobs/trigger", json=self.BODY).status_code, 401)
        self.assertEqual(self.client.post("/api/v1/jobs/trigger", json=self.BODY, headers=self.auth).status_code, 401)
        bad = self.client.post("/api/v1/jobs/trigger", json=self.BODY, headers={"X-Internal-Job-Token": "nope"})
        self.assertEqual(bad.status_code, 401)
        ok = self.client.post("/api/v1/jobs/trigger", json=self.BODY,
                              headers={"X-Internal-Job-Token": "test-internal-job-token"})
        self.assertEqual(ok.status_code, 200)

    def test_unset_job_token_rejects_everything(self) -> None:
        client = TestClient(create_app(owner_settings(internal_job_token="")))
        for value in ("", "anything"):
            r = client.post("/api/v1/jobs/trigger", json=self.BODY, headers={"X-Internal-Job-Token": value})
            self.assertEqual(r.status_code, 401)


# ---------------------------------------------------------------------------------------------
class TestHardeningAndGuardrails(SecurityTestBase):
    def test_security_headers_present(self) -> None:
        r = self.client.get("/api/v1/health")
        self.assertEqual(r.headers.get("x-content-type-options"), "nosniff")
        self.assertEqual(r.headers.get("x-frame-options"), "DENY")
        self.assertEqual(r.headers.get("cache-control"), "no-store")
        self.assertEqual(r.headers.get("referrer-policy"), "no-referrer")

    def test_cors_does_not_allow_credentials_and_only_listed_origin(self) -> None:
        ok = self.client.options("/api/v1/health", headers={
            "Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"})
        self.assertEqual(ok.headers.get("access-control-allow-origin"), "http://localhost:5173")
        self.assertIsNone(ok.headers.get("access-control-allow-credentials"))
        evil = self.client.options("/api/v1/health", headers={
            "Origin": "https://evil.example", "Access-Control-Request-Method": "GET"})
        self.assertIsNone(evil.headers.get("access-control-allow-origin"))

    def test_scanner_symbol_count_is_capped(self) -> None:
        symbols = ",".join(f"SYM{i}" for i in range(31))
        r = self.client.get(f"/api/v1/scanner?symbols={symbols}", headers=self.auth)
        self.assertEqual(r.status_code, 422)

    def test_telegram_message_text_is_html_escaped(self) -> None:
        notifier = TelegramNotifier("123:token", "456", live_http=True)
        captured = {}

        class Resp:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return b'{"ok": true}'

        def fake_urlopen(req, timeout=0):
            captured["body"] = json.loads(req.data.decode())
            return Resp()

        with patch("urllib.request.urlopen", fake_urlopen):
            notifier.send_closed_candle_alert("<b>X</b>", '<a href="http://evil">click</a> & more', "2026-11-18T10:00+05:30")
        text = captured["body"]["text"]
        self.assertNotIn("<a href", text)
        self.assertIn("&lt;a href", text)
        self.assertIn("&lt;B&gt;X&lt;/B&gt;", text)  # symbol is upper-cased, then escaped

    def test_status_endpoint_never_exposes_secret_values(self) -> None:
        s = owner_settings(angel_api_key="ANGEL-SECRET-VALUE", gemini_api_key="GEMINI-SECRET-VALUE",
                           telegram_bot_token="TG-SECRET-VALUE", telegram_chat_id="123", internal_job_token="JOB-SECRET-VALUE",
                           supabase_service_role_key="SVC-SECRET-VALUE")
        client = TestClient(create_app(s))
        headers = owner_headers(s)
        blob = "".join(client.get(u, headers=headers).text for u in (
            "/api/v1/system/status", "/api/v1/integrations/status", "/api/v1/providers/diagnostics",
            "/api/v1/notifications/telegram/status"))
        for secret in ("ANGEL-SECRET-VALUE", "GEMINI-SECRET-VALUE", "TG-SECRET-VALUE", "JOB-SECRET-VALUE",
                       "SVC-SECRET-VALUE", AUTH_JWT_SECRET, SUPABASE_JWT_SECRET, "scrypt$"):
            self.assertNotIn(secret, blob)
        self.assertNotIn("scrypt$", repr(s))
        self.assertNotIn(AUTH_JWT_SECRET, repr(s))

    def test_live_order_execution_stays_disabled(self) -> None:
        status = self.client.get("/api/v1/system/status", headers=self.auth).json()
        self.assertEqual(status["execution_mode"], "PAPER_ONLY")
        self.assertFalse(status["live_order_execution"])
        refused = self.client.post("/api/v1/assistant/query", headers=self.auth,
                                   json={"question": "place live order with real money", "symbol": "RELIANCE"})
        self.assertEqual(refused.json()["mode"], "REFUSAL_GUARDRAIL")
        blocked = self.client.post("/api/v1/assistant/tool", headers=self.auth,
                                   json={"tool_name": "place_live_broker_order", "params": {}})
        self.assertEqual(blocked.status_code, 403)

    def test_no_broker_order_api_exists_in_backend_source_or_routes(self) -> None:
        """Static check: no SmartAPI order-placement call sites and no order routes. Not a proof of absence at runtime."""
        forbidden = re.compile(r"placeOrder|placeorder|place_order\(|modifyOrder|cancelOrder|gttCreateRule|"
                               r"createOrder|\.orderBook|SmartConnect\(.*\)\.(place|modify|cancel)", re.I)
        app_dir = Path(__file__).resolve().parents[1] / "app"
        offenders = []
        for py in app_dir.rglob("*.py"):
            for lineno, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
                if forbidden.search(line) and not line.lstrip().startswith("#"):
                    offenders.append(f"{py.relative_to(app_dir)}:{lineno}: {line.strip()}")
        self.assertEqual(offenders, [])
        for _, path in _api_operations(self.app):
            self.assertNotRegex(path, r"(?i)/(orders?|execute|broker)(/|$)")


# ---------------------------------------------------------------------------------------------
class TestSecurityConfigValidation(unittest.TestCase):
    PROD = {"APP_ENV": "production", "DEMO_MODE": "true", "ALLOWED_ORIGINS": "https://app.example.com",
            "SUPABASE_URL": "https://x.supabase.co", "SUPABASE_JWT_SECRET": "s" * 40}

    def test_production_requires_an_owner_auth_method(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            load_settings_from_env(dict(self.PROD))
        self.assertIn("owner authentication", str(ctx.exception).lower())

    def test_production_ok_with_local_login_or_supabase_owner(self) -> None:
        local = load_settings_from_env({**self.PROD, "OWNER_PASSWORD_HASH": hash_password("a-long-enough-password"),
                                        "AUTH_JWT_SECRET": "j" * 48})
        self.assertTrue(local.local_auth_enabled)
        sb = load_settings_from_env({**self.PROD, "OWNER_USER_ID": OWNER_UUID})
        self.assertTrue(sb.supabase_auth_enabled)
        self.assertFalse(sb.local_auth_enabled)

    def test_weak_or_inconsistent_auth_settings_rejected(self) -> None:
        h = hash_password("a-long-enough-password")
        base = {"APP_ENV": "development"}
        cases = [
            ({"AUTH_JWT_SECRET": "short"}, "at least 32"),
            ({"OWNER_PASSWORD_HASH": h}, "AUTH_JWT_SECRET is required"),
            ({"OWNER_PASSWORD_HASH": "plaintext-password", "AUTH_JWT_SECRET": "j" * 48}, "not a valid hash"),
            ({"AUTH_JWT_SECRET": "j" * 48, "SUPABASE_JWT_SECRET": "j" * 48}, "must differ"),
            ({"ACCESS_TOKEN_TTL_MINUTES": "100000"}, "between 5 and 1440"),
            ({"ACCESS_TOKEN_TTL_MINUTES": "abc"}, "must be an integer"),
            ({"ALLOWED_ORIGINS": "*"}, "Wildcard"),
        ]
        for extra, needle in cases:
            with self.assertRaises(ValueError, msg=str(extra)) as ctx:
                load_settings_from_env({**base, **extra})
            self.assertIn(needle, str(ctx.exception), str(extra))

    def test_api_docs_default_off_and_env_opt_in(self) -> None:
        self.assertFalse(load_settings_from_env({"APP_ENV": "development"}).enable_api_docs)
        self.assertTrue(load_settings_from_env({"APP_ENV": "development", "ENABLE_API_DOCS": "true"}).enable_api_docs)

    def test_no_hardcoded_secrets_in_application_source(self) -> None:
        pattern = re.compile(r"""(?i)(api[_-]?key|secret|password|token)\s*=\s*["'][A-Za-z0-9_\-\.]{16,}["']""")
        app_dir = Path(__file__).resolve().parents[1] / "app"
        hits = []
        for py in app_dir.rglob("*.py"):
            for n, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
                if pattern.search(line) and "YOUR_" not in line:
                    hits.append(f"{py.relative_to(app_dir)}:{n}")
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
