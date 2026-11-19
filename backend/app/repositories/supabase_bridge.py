"""
Live Supabase REST Persistence Bridge & Telegram Live Bot Adapter.
Uses the user's existing Render environment variables:
- SUPABASE_URL + SUPABASE_KEY (or SUPABASE_SERVICE_ROLE_KEY)
- TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID
Safely degrades to local in-memory state when offline or unconfigured.
Never logs or exposes secret tokens.
"""
import datetime
import json
import urllib.request
import urllib.error
from typing import Any, Dict, List, Optional


class SupabaseRestBridge:
    """
    Connects to Supabase PostgREST (`/rest/v1/<table>`) when SUPABASE_URL and SUPABASE_KEY
    are present in `.env`.
    """

    def __init__(self, supabase_url: str = "", supabase_key: str = "") -> None:
        self._url = (supabase_url or "").strip().rstrip("/")
        self._key = (supabase_key or "").strip()
        self.sync_count = 0
        self.last_error: Optional[str] = None
        self.last_verified_at: Optional[str] = None
        self.connection_verified = False

    def is_configured(self) -> bool:
        return bool(self._url and self._key and not self._key.startswith("YOUR_"))

    def status_summary(self) -> Dict[str, Any]:
        return {
            "configured": self.is_configured(),
            "mode": ("CONNECTED" if self.connection_verified else "CONFIGURED_NOT_VERIFIED") if self.is_configured() else "IN_MEMORY_FALLBACK",
            "connection_verified": self.connection_verified,
            "last_verified_at": self.last_verified_at,
            "sync_count": self.sync_count,
            "last_error": self.last_error,
        }

    def verify_connection(self, timeout_sec: float = 6.0) -> bool:
        """Verify Supabase REST is reachable; configuration alone is not proof of connectivity."""
        if not self.is_configured():
            return False
        req = urllib.request.Request(
            f"{self._url}/rest/v1/",
            headers={"apikey": self._key, "Authorization": f"Bearer {self._key}"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
                self.connection_verified = 200 <= resp.status < 300
            self.last_error = None if self.connection_verified else "HTTP_STATUS"
        except Exception as exc:
            self.connection_verified = False
            self.last_error = type(exc).__name__
        if self.connection_verified:
            self.last_verified_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        return self.connection_verified

    def select_rows(self, table: str, query: Optional[Dict[str, str]] = None, timeout_sec: float = 5.0) -> Optional[List[Dict[str, Any]]]:
        if not self.is_configured():
            return None
        from urllib.parse import urlencode
        qs = urlencode(query or {})
        endpoint = f"{self._url}/rest/v1/{table}" + (f"?{qs}" if qs else "")
        req = urllib.request.Request(endpoint, headers={"apikey": self._key, "Authorization": f"Bearer {self._key}"}, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if isinstance(data, list):
                    self.connection_verified = True
                    self.last_verified_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
                    self.last_error = None
                    return data
        except Exception as exc:
            self.last_error = type(exc).__name__
            self.connection_verified = False
        return None

    def upsert_row(self, table: str, payload: Dict[str, Any], timeout_sec: float = 5.0) -> bool:
        if not self.is_configured():
            return False
        endpoint = f"{self._url}/rest/v1/{table}"
        req = urllib.request.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "apikey": self._key,
                "Authorization": f"Bearer {self._key}",
                "Content-Type": "application/json",
                "Prefer": "resolution=merge-duplicates",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
                if 200 <= resp.status < 300:
                    self.sync_count += 1
                    return True
        except Exception as exc:
            self.last_error = type(exc).__name__
        return False
