"""
Source-level guards for the single-owner login UI (React app + backend-served fallback page).
Pure file checks: they need no running server and no browser, so they are cheap to keep in CI.

They pin the security-relevant behaviour of the UI:
  * login goes to POST /api/v1/auth/login with only {"password": ...}
  * the session token lives in sessionStorage - never localStorage
  * private requests go through the authenticated client (Bearer token, 401 => back to login)
  * no hard-coded password / JWT in the UI sources
  * the fallback page hides every private control until a session exists
"""
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
FE_SRC = REPO / "frontend" / "src"
STATIC_INDEX = REPO / "backend" / "app" / "static" / "index.html"


def _fe_files():
    return [p for p in FE_SRC.rglob("*") if p.suffix in (".ts", ".tsx")]


class ReactOwnerLoginContract(unittest.TestCase):
    def test_login_screen_and_api_client_exist(self) -> None:
        self.assertTrue((FE_SRC / "components" / "LoginScreen.tsx").exists())
        api = (FE_SRC / "lib" / "api.ts").read_text(encoding="utf-8")
        self.assertIn("/api/v1/auth/login", api)
        self.assertIn("JSON.stringify({ password })", api)
        self.assertIn("setAuthToken(token)", api)
        self.assertIn("access_token", api)
        self.assertIn("sessionStorage", api)

    def test_no_local_storage_anywhere_in_frontend_sources(self) -> None:
        for p in _fe_files():
            code = re.sub(r"/\*.*?\*/|//[^\n]*", "", p.read_text(encoding="utf-8"), flags=re.S)
            self.assertNotIn("localStorage", code, f"{p} must not use localStorage")

    def test_no_hardcoded_jwt_or_password(self) -> None:
        for p in _fe_files():
            txt = p.read_text(encoding="utf-8")
            self.assertIsNone(re.search(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.", txt), f"JWT literal in {p}")
            self.assertIsNone(re.search(r"password\s*[:=]\s*['\"][^'\"]+['\"]", txt, re.I), f"password literal in {p}")

    def test_private_calls_use_authenticated_client_not_raw_fetch(self) -> None:
        for p in _fe_files():
            if p.name == "api.ts":
                continue
            code = re.sub(r"/\*.*?\*/|//[^\n]*", "", p.read_text(encoding="utf-8"), flags=re.S)
            self.assertIsNone(re.search(r"\bfetch\s*\(", code), f"{p} must call the API through lib/api.ts, not raw fetch()")

    def test_401_clears_session_and_app_is_gated(self) -> None:
        api = (FE_SRC / "lib" / "api.ts").read_text(encoding="utf-8")
        self.assertRegex(api, r"response\.status === 401[\s\S]{0,80}clearSession\(\)[\s\S]{0,40}notifyUnauthorized\(\)")
        self.assertIn("if (!authToken)", api)  # no private network call without a token
        app = (FE_SRC / "app" / "App.tsx").read_text(encoding="utf-8")
        self.assertIn("<LoginScreen", app)
        self.assertIn("onUnauthorized(", app)
        self.assertIn("<AuthenticatedApp", app)

    def test_no_signup_or_multi_user_ui(self) -> None:
        for name in ("LoginScreen.tsx", "App.tsx"):
            txt = (FE_SRC / ("components" if name == "LoginScreen.tsx" else "app") / name).read_text(encoding="utf-8")
            code = re.sub(r"/\*.*?\*/|//[^\n]*", "", txt, flags=re.S)  # ignore explanatory comments
            self.assertNotRegex(code, r"(?i)create account|\bsign[ -]?up\b|\bregister\b|forgot password")


class StaticFallbackPageContract(unittest.TestCase):
    def setUp(self) -> None:
        self.html = STATIC_INDEX.read_text(encoding="utf-8")

    def test_login_flow_and_session_storage(self) -> None:
        self.assertIn("/api/v1/auth/login", self.html)
        self.assertIn("JSON.stringify({ password: pwd.value })", self.html)
        self.assertIn("sessionStorage", self.html)
        self.assertNotIn("localStorage", self.html)
        self.assertIn("'Authorization': 'Bearer ' + authToken", self.html)

    def test_private_controls_hidden_until_authenticated_without_css_framework(self) -> None:
        self.assertRegex(self.html, r'<div id="app-view" hidden')
        self.assertIn("[hidden]{display:none !important}", self.html)

    def test_no_unauthenticated_private_fetch(self) -> None:
        # Exactly two raw fetch() calls: the login POST and the shared authenticated helper.
        calls = re.findall(r"\bfetch\(", self.html)
        self.assertEqual(len(calls), 2, "only the login request and apiFetch may call fetch()")
        self.assertIn("if (!authToken) { showLogin(", self.html)  # apiFetch refuses to run without a token
        self.assertIn("res.status === 401", self.html)

    def test_no_hardcoded_credentials(self) -> None:
        self.assertIsNone(re.search(r"eyJ[A-Za-z0-9_-]{10,}\.", self.html))


if __name__ == "__main__":
    unittest.main()
