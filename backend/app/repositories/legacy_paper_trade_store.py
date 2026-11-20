"""Compatibility persistence adapter for the user's existing ApexQuant Supabase `paper_trades` table.

This keeps the old single-owner table usable while the normalized TRADEIQ schema migration is pending.
The Supabase key is backend-only. This adapter does not provide multi-user isolation (the legacy table has
no user column and the service key bypasses RLS). That is acceptable ONLY because every endpoint that reaches
it is behind single-owner authentication (see core/auth.py); never call it from an unauthenticated path.
"""
import datetime
import json
import urllib.parse
import urllib.request
from decimal import Decimal
from typing import Any, Dict, List, Optional


class LegacyPaperTradeStore:
    def __init__(self, supabase_url: str = "", supabase_key: str = "") -> None:
        self.url = (supabase_url or "").strip().rstrip("/")
        self.key = (supabase_key or "").strip()
        self.last_error: Optional[str] = None
        self.last_operation: Optional[str] = None

    def is_configured(self) -> bool:
        return bool(self.url and self.key and not self.key.startswith("YOUR_"))

    def _request(self, method: str, path: str, payload: Optional[Dict[str, Any]] = None, prefer: str = "") -> Any:
        if not self.is_configured():
            raise RuntimeError("Legacy Supabase paper_trades store is not configured")
        headers = {"apikey": self.key, "Authorization": f"Bearer {self.key}", "Accept": "application/json"}
        data = None
        if payload is not None:
            data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if prefer:
            headers["Prefer"] = prefer
        req = urllib.request.Request(f"{self.url}/rest/v1/{path}", data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=8.0) as resp:
            raw = resp.read()
            return json.loads(raw.decode("utf-8")) if raw else []

    def fetch_nse_positions(self) -> List[Dict[str, Any]]:
        try:
            rows = self._request("GET", "paper_trades?select=*&order=created_at.desc")
            self.last_error = None
            self.last_operation = "fetch"
            return [r for r in rows if str(r.get("market", "NSE")).upper() == "NSE"]
        except Exception as exc:
            self.last_error = type(exc).__name__
            self.last_operation = "fetch_failed"
            return []

    def insert_position(self, position: Any) -> Dict[str, Any]:
        row = {
            "id": position.position_id,
            "symbol": position.symbol,
            "market": "NSE",
            "budget_inr": float(position.entry_fill_price * Decimal(position.quantity)),
            "buy_price_inr": float(position.entry_fill_price),
            "target_inr": float(position.target_price),
            "stop_loss_inr": float(position.stop_loss),
            "hold_label": f"{position.trading_mode}|{getattr(position, 'engine_code', 'MANUAL')}|{getattr(position, 'direction', 'LONG')}",
            "status": "HOLDING",
            "created_at": int(datetime.datetime.now(datetime.timezone.utc).timestamp()),
        }
        try:
            result = self._request("POST", "paper_trades", row, prefer="return=representation")
            self.last_error = None
            self.last_operation = "insert"
            return {"persisted": True, "row": result[0] if isinstance(result, list) and result else row}
        except Exception as exc:
            # Older versions of the user's table may have only this original base column set.
            base = {k: row[k] for k in ("id", "symbol", "budget_inr", "buy_price_inr", "target_inr", "stop_loss_inr", "status", "created_at")}
            try:
                result = self._request("POST", "paper_trades", base, prefer="return=representation")
                self.last_error = None
                self.last_operation = "insert_legacy_columns"
                return {"persisted": True, "row": result[0] if isinstance(result, list) and result else base, "compatibility_mode": True}
            except Exception as exc2:
                self.last_error = type(exc2).__name__
                self.last_operation = "insert_failed"
                return {"persisted": False, "error": self.last_error}

    def close_position(self, position: Any) -> Dict[str, Any]:
        outcome = "WIN" if position.net_pnl_inr >= Decimal("0") else "LOSS"
        query = urllib.parse.urlencode({"id": f"eq.{position.position_id}"})
        patch = {"status": outcome, "exit_price_inr": float(position.exit_fill_price or 0),
                 "closed_at": int(datetime.datetime.now(datetime.timezone.utc).timestamp())}
        try:
            self._request("PATCH", f"paper_trades?{query}", patch)
            self.last_error = None
            self.last_operation = "close"
            return {"persisted": True, "status": outcome}
        except Exception:
            try:
                self._request("PATCH", f"paper_trades?{query}", {"status": outcome})
                self.last_error = None
                self.last_operation = "close_legacy_columns"
                return {"persisted": True, "status": outcome, "compatibility_mode": True}
            except Exception as exc:
                self.last_error = type(exc).__name__
                self.last_operation = "close_failed"
                return {"persisted": False, "error": self.last_error}


    def delete_position(self, position_id: str) -> Dict[str, Any]:
        """Explicit manual delete only — never called automatically on deploy or restart."""
        if not self.is_configured():
            return {"deleted": False, "error": "NOT_CONFIGURED"}
        query = urllib.parse.urlencode({"id": f"eq.{position_id}"})
        try:
            self._request("DELETE", f"paper_trades?{query}")
            self.last_error = None
            self.last_operation = "delete"
            return {"deleted": True, "id": position_id}
        except Exception as exc:
            self.last_error = type(exc).__name__
            self.last_operation = "delete_failed"
            return {"deleted": False, "error": self.last_error}

    def status_summary(self) -> Dict[str, Any]:
        return {"configured": self.is_configured(), "table": "paper_trades", "last_operation": self.last_operation,
                "last_error": self.last_error, "mode": "LEGACY_COMPATIBILITY" if self.is_configured() else "NOT_CONFIGURED"}