"""
Milestone 4.1 & 4.5 — Provider-Independent LLM Interface, Multi-Key Gemini Pool Client
(migrated & hardened from `gemini_pool.py`), and Fixed Read-Only Assistant Toolkit.
"""
import json
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional


class LLMProvider(ABC):
    """Provider-independent LLM interface (Section 5 & Milestone 4.1)."""

    @abstractmethod
    def generate_structured_json(self, system_prompt: str, user_prompt: str, timeout_sec: float = 10.0) -> Dict[str, Any]:
        raise NotImplementedError


class GeminiLLMClient(LLMProvider):
    """
    Production Gemini REST adapter (backend-only) with Multi-Key Round-Robin Pool & 429 Cooldown
    (migrated from the user's `gemini_pool.py`).
    - Supports single `GEMINI_API_KEY` or comma-separated `GEMINI_API_KEYS`.
    - Automatically rotates keys on HTTP 429 / quota errors with a 90s cooldown per key.
    - Enforces request payload size limits (max 32 KB).
    - Never logs or exposes any API key.
    """

    MAX_PROMPT_BYTES = 32_768
    COOLDOWN_SECONDS = 90.0

    def __init__(
        self,
        api_key: str = "",
        api_keys: Optional[List[str]] = None,
        model_name: str = "gemini-2.5-flash",
        timeout_sec: float = 10.0,
    ) -> None:
        pool = list(api_keys or [])
        if api_key and api_key not in pool and not api_key.startswith("YOUR_"):
            pool.insert(0, api_key)
        self._keys: List[str] = [k for k in pool if k and not k.startswith("YOUR_")]
        self._cooldowns: Dict[str, float] = {}
        self._rr_index: int = 0
        self.model_name = model_name
        self.default_timeout_sec = max(25.0, timeout_sec)
        self.request_count = 0
        self.error_count = 0
        self.last_latency_ms: float = 0.0
        self.last_error: Optional[str] = None

    def is_configured(self) -> bool:
        return len(self._keys) > 0

    def pool_size(self) -> int:
        return len(self._keys)

    def _pick_available_key(self) -> Optional[str]:
        if not self._keys:
            return None
        now = time.monotonic()
        n = len(self._keys)
        for _ in range(n):
            k = self._keys[self._rr_index % n]
            self._rr_index = (self._rr_index + 1) % n
            if self._cooldowns.get(k, 0.0) <= now:
                return k
        # If all keys are in cooldown, pick the one with the earliest expiry
        return min(self._keys, key=lambda k: self._cooldowns.get(k, 0.0))

    @staticmethod
    def _extract_json(text: str) -> Dict[str, Any]:
        s = (text or "").strip()
        if s.startswith("```"):
            lines = s.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            s = "\n".join(lines).strip()
        try:
            parsed = json.loads(s)
            return parsed if isinstance(parsed, dict) else {"answer": s}
        except Exception:
            start = s.find("{")
            end = s.rfind("}")
            if start != -1 and end > start:
                parsed = json.loads(s[start : end + 1])
                if isinstance(parsed, dict):
                    return parsed
            return {"answer": s}

    def generate_structured_json(self, system_prompt: str, user_prompt: str, timeout_sec: float = 25.0) -> Dict[str, Any]:
        combined_len = len((system_prompt + user_prompt).encode("utf-8"))
        if combined_len > self.MAX_PROMPT_BYTES:
            raise ValueError(f"LLM prompt size ({combined_len} bytes) exceeds limit ({self.MAX_PROMPT_BYTES} bytes)")
        if not self.is_configured():
            raise RuntimeError("GEMINI_API_KEY / GEMINI_API_KEYS not configured; using deterministic fallback.")

        models_to_try = list(dict.fromkeys([self.model_name, "gemini-2.5-flash", "gemini-2.0-flash"]))
        last_err: Optional[Exception] = None
        for _attempt in range(max(1, len(self._keys))):
            key = self._pick_available_key()
            if not key:
                break
            for mdl in models_to_try:
                self.request_count += 1
                t0 = time.monotonic()
                url = (
                    f"https://generativelanguage.googleapis.com/v1beta/models/{mdl}:generateContent"
                    f"?key={key}"
                )
                payload = {
                    "systemInstruction": {"parts": [{"text": system_prompt}]},
                    "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
                    "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"},
                }
                req = urllib.request.Request(
                    url,
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                try:
                    with urllib.request.urlopen(req, timeout=max(20.0, timeout_sec or self.default_timeout_sec)) as resp:
                        raw = json.loads(resp.read().decode("utf-8"))
                        parts = raw["candidates"][0]["content"]["parts"]
                        text = "".join(str(p.get("text", "")) for p in parts if isinstance(p, dict))
                        self.last_latency_ms = round((time.monotonic() - t0) * 1000.0, 2)
                        self.last_error = None
                        self.model_name = mdl
                        return self._extract_json(text)
                except urllib.error.HTTPError as hexc:
                    self.error_count += 1
                    self.last_latency_ms = round((time.monotonic() - t0) * 1000.0, 2)
                    err_body = ""
                    try:
                        err_body = hexc.read().decode("utf-8", errors="ignore")[:160]
                    except Exception:
                        pass
                    self.last_error = f"HTTP {hexc.code} ({mdl}): {err_body or hexc.reason}"
                    if hexc.code == 404:
                        last_err = hexc
                        continue  # try fallback model with same key
                    if hexc.code in (429, 401, 403):
                        self._cooldowns[key] = time.monotonic() + self.COOLDOWN_SECONDS
                    last_err = hexc
                    break
                except Exception as exc:
                    self.error_count += 1
                    self.last_latency_ms = round((time.monotonic() - t0) * 1000.0, 2)
                    self.last_error = f"{type(exc).__name__} ({mdl}): {str(exc)[:120]}"
                    last_err = exc
                    break
        raise RuntimeError(self.last_error or "All Gemini keys failed or timed out safely") from last_err


class ReadOnlyAssistantToolkit:
    """
    Section 16 & Milestone 4.5 — Fixed Read-Only Tools for the AI Assistant.
    The assistant can ONLY invoke:
      - get_analysis(symbol, trading_mode)
      - get_plan(symbol, trading_mode, strategy_code)
      - calc_quantity(capital_inr, entry_price, stop_loss, target_price, trading_mode)
      - get_journal_stats()
    It has ZERO database write access and ZERO broker execution access.
    """

    ALLOWED_TOOLS = ("get_analysis", "get_plan", "calc_quantity", "get_journal_stats")

    def __init__(
        self,
        analysis_fn: Callable[[str, str], Dict[str, Any]],
        plan_fn: Callable[[str, str, str], Dict[str, Any]],
        calc_qty_fn: Callable[[Decimal, Decimal, Decimal, Decimal, str], Dict[str, Any]],
        journal_stats_fn: Callable[[], Dict[str, Any]],
    ) -> None:
        self._analysis_fn = analysis_fn
        self._plan_fn = plan_fn
        self._calc_qty_fn = calc_qty_fn
        self._journal_stats_fn = journal_stats_fn

    def invoke_tool(self, tool_name: str, **kwargs: Any) -> Dict[str, Any]:
        if tool_name not in self.ALLOWED_TOOLS:
            raise PermissionError(
                f"Tool '{tool_name}' is blocked. Only read-only tools {self.ALLOWED_TOOLS} are permitted."
            )
        if tool_name == "get_analysis":
            return self._analysis_fn(str(kwargs.get("symbol", "RELIANCE")), str(kwargs.get("trading_mode", "SWING")))
        if tool_name == "get_plan":
            return self._plan_fn(
                str(kwargs.get("symbol", "RELIANCE")),
                str(kwargs.get("trading_mode", "SWING")),
                str(kwargs.get("strategy_code", "BREAKOUT_RETEST")),
            )
        if tool_name == "calc_quantity":
            return self._calc_qty_fn(
                Decimal(str(kwargs.get("capital_inr", "75000.00"))),
                Decimal(str(kwargs.get("entry_price", "1300.00"))),
                Decimal(str(kwargs.get("stop_loss", "1280.00"))),
                Decimal(str(kwargs.get("target_price", "1360.00"))),
                str(kwargs.get("trading_mode", "SWING")),
            )
        return self._journal_stats_fn()
