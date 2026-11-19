"""
Section 5, 18, 35 & Milestone 6.3 — Protected Background Jobs (`INTERNAL_JOB_TOKEN`)
and Live Telegram Notification Adapter (`TelegramNotifier`) compatible with
`TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` from the user's Render setup.
"""
import datetime
import hmac
import html
import json
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class JobRunRecord:
    job_name: str
    status: str
    executed_at_utc: str
    details: Dict[str, Any]


class ProtectedJobRunner:
    """
    Executes scheduled maintenance jobs (instrument master refresh, 5m candle ingestion,
    stale/expired trade plan sweep, closed-candle alert evaluation, 9:00 AM IST pre-market report)
    only when a valid `X-Internal-Job-Token` header matching `INTERNAL_JOB_TOKEN` is supplied.
    """

    def __init__(self, expected_token: str) -> None:
        self._expected_token = expected_token
        self.history: List[JobRunRecord] = []

    def verify_job_token(self, supplied_token: Optional[str]) -> bool:
        if not self._expected_token or not supplied_token:
            return False
        return hmac.compare_digest(self._expected_token.encode("utf-8"), supplied_token.encode("utf-8"))

    def run_job(self, job_name: str, details: Dict[str, Any]) -> JobRunRecord:
        rec = JobRunRecord(
            job_name=job_name,
            status="COMPLETED",
            executed_at_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            details=details,
        )
        self.history.append(rec)
        return rec


class TelegramNotifier:
    """
    Milestone 6.3 — Live & Optional Telegram notification channel.
    - Automatically activates when `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are set in `.env`.
    - Can send live HTTP POST requests to `https://api.telegram.org/bot<TOKEN>/sendMessage`
      or run in dry-run test mode when `live_http=False`.
    - Never exposes `bot_token` in logs or API responses.
    """

    def __init__(self, bot_token: str = "", chat_id: str = "", enabled: Optional[bool] = None, live_http: bool = True) -> None:
        self._bot_token = (bot_token or "").strip()
        self._chat_id = (chat_id or "").strip()
        has_creds = bool(self._bot_token and self._chat_id and not self._bot_token.startswith("YOUR_"))
        self.enabled = has_creds if enabled is None else bool(enabled and has_creds)
        self.live_http = live_http
        self.sent_messages: List[Dict[str, str]] = []

    def status_summary(self) -> Dict[str, Any]:
        return {
            "channel": "TELEGRAM",
            "enabled": self.enabled,
            "configured": bool(self._bot_token and self._chat_id and not self._bot_token.startswith("YOUR_")),
            "delivery_mode": "LIVE_HTTP" if self.live_http and self.enabled else "NOT_CONFIGURED_OR_DRY_RUN",
            "sent_count": len(self.sent_messages),
        }

    def send_closed_candle_alert(self, symbol: str, message: str, candle_ts_ist: str) -> Dict[str, Any]:
        if not self.enabled:
            return {"delivered": False, "reason": "TELEGRAM_DISABLED_OR_UNCONFIGURED"}
        # Escape caller-supplied text: parse_mode=HTML would otherwise interpret injected markup.
        text = (
            f"<b>TRADEIQ [PAPER ONLY] — {html.escape(symbol.upper())}</b>\n"
            f"{html.escape(message)}\n<i>Candle IST: {html.escape(candle_ts_ist)}</i>"
        )
        delivered_remote = False
        if self.live_http:
            url = f"https://api.telegram.org/bot{self._bot_token}/sendMessage"
            payload = {"chat_id": self._chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=8.0) as resp:
                    body = json.loads(resp.read().decode("utf-8"))
                    delivered_remote = (200 <= resp.status < 300) and bool(body.get("ok"))
            except Exception:
                delivered_remote = False
        record = {
            "symbol": symbol.upper(),
            "message": message,
            "candle_ts_ist": candle_ts_ist,
            "delivered_remote": str(delivered_remote),
            "sent_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        self.sent_messages.append(record)
        return {"delivered": delivered_remote, "record": record, "reason": None if delivered_remote else "TELEGRAM_DELIVERY_FAILED"}
