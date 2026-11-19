"""
Single-owner authentication for TRADEIQ.

Two ways to obtain an owner credential (both resolve to the SAME single identity, `settings.owner_id`):
  1. Built-in owner login:  POST /api/v1/auth/login  (password checked against OWNER_PASSWORD_HASH,
     session JWT signed with AUTH_JWT_SECRET).
  2. Supabase access token (HS256, SUPABASE_JWT_SECRET) whose `sub` equals OWNER_USER_ID.

Security properties:
  * Fail closed: if neither method is configured, every private endpoint answers 401.
  * Algorithm pinned to HS256; signature, `exp` (required), `nbf`, `sub`, `aud`/`iss` are all checked.
  * A valid token for any identity other than the owner is rejected with 403.
  * The caller's identity comes ONLY from the verified token — never from request bodies/queries.
"""
import base64
import collections
import hashlib
import hmac
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, Deque, Dict, Optional, Tuple
from fastapi import Header, Request

from .errors import TradeIQException
from .logging import configure_logging

logger = configure_logging()

LOCAL_ISSUER = "tradeiq-owner-session"
LOCAL_AUDIENCE = "tradeiq-api"
SUPABASE_AUDIENCE = "authenticated"
_CLOCK_SKEW_SEC = 30


@dataclass(frozen=True)
class AuthenticatedUser:
    user_id: str
    email: str = ""
    role: str = "authenticated"
    auth_method: str = "unknown"


def _unauthorized(message: str = "Authentication required") -> TradeIQException:
    return TradeIQException("UNAUTHORIZED", message, status_code=401, headers={"WWW-Authenticate": "Bearer"})


def _b64url_decode(data: str) -> bytes:
    rem = len(data) % 4
    if rem:
        data += "=" * (4 - rem)
    return base64.urlsafe_b64decode(data.encode("ascii"))


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def create_hs256_jwt(payload: dict, secret: str) -> str:
    """Helper for tests and local dev token generation."""
    header = {"alg": "HS256", "typ": "JWT"}
    h_b64 = _b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    p_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{h_b64}.{p_b64}".encode("ascii")
    sig = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{h_b64}.{p_b64}.{_b64url_encode(sig)}"


def verify_hs256_jwt(token: str, secret: str) -> dict:
    if not secret:
        raise TradeIQException("AUTH_CONFIG_ERROR", "JWT secret is not configured on backend", status_code=500)
    parts = token.strip().split(".")
    if len(parts) != 3:
        raise _unauthorized("Malformed bearer token")
    h_b64, p_b64, s_b64 = parts
    try:
        header = json.loads(_b64url_decode(h_b64).decode("utf-8"))
        payload = json.loads(_b64url_decode(p_b64).decode("utf-8"))
        provided_sig = _b64url_decode(s_b64)
    except Exception:
        raise _unauthorized("Invalid token encoding")
    if not isinstance(header, dict) or not isinstance(payload, dict):
        raise _unauthorized("Invalid token structure")

    if header.get("alg") != "HS256":
        raise _unauthorized("Unsupported JWT algorithm")

    signing_input = f"{h_b64}.{p_b64}".encode("ascii")
    expected_sig = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    if not hmac.compare_digest(provided_sig, expected_sig):
        raise _unauthorized("Invalid token signature")

    now = time.time()
    try:
        exp = payload.get("exp")
        if exp is None or isinstance(exp, bool) or float(exp) < now:
            raise _unauthorized("Token has expired")
        nbf = payload.get("nbf")
        if nbf is not None and float(nbf) > now + _CLOCK_SKEW_SEC:
            raise _unauthorized("Token is not yet valid")
    except (TypeError, ValueError):
        raise _unauthorized("Invalid token time claims")

    sub = payload.get("sub")
    if not isinstance(sub, str) or not sub.strip():
        raise _unauthorized("Token missing subject (sub)")

    return payload


def _peek_issuer(token: str) -> Optional[str]:
    """Reads `iss` WITHOUT trusting it — only used to pick which secret to verify against."""
    try:
        payload = json.loads(_b64url_decode(token.split(".")[1]).decode("utf-8"))
        iss = payload.get("iss") if isinstance(payload, dict) else None
        return iss if isinstance(iss, str) else None
    except Exception:
        return None


def _aud_contains(aud: Any, expected: str) -> bool:
    if isinstance(aud, str):
        return aud == expected
    if isinstance(aud, list):
        return expected in aud
    return False


def issue_owner_token(settings, now: Optional[float] = None) -> Tuple[str, int]:
    """Creates a short-lived owner session JWT. Returns (token, expires_in_seconds)."""
    if not settings.local_auth_enabled:
        raise TradeIQException("AUTH_NOT_CONFIGURED", "Owner login is not configured on this server", status_code=503)
    issued = int(now if now is not None else time.time())
    ttl = int(settings.access_token_ttl_minutes) * 60
    claims = {
        "iss": LOCAL_ISSUER,
        "aud": LOCAL_AUDIENCE,
        "sub": settings.owner_id,
        "role": "owner",
        "iat": issued,
        "nbf": issued,
        "exp": issued + ttl,
        "jti": uuid.uuid4().hex,
    }
    return create_hs256_jwt(claims, settings.auth_jwt_secret), ttl


def _extract_bearer(authorization: Optional[str]) -> str:
    if not authorization:
        raise _unauthorized("Missing or invalid Authorization Bearer header")
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        raise _unauthorized("Missing or invalid Authorization Bearer header")
    return value.strip()


async def get_current_user(
    request: Request,
    authorization: Optional[str] = Header(default=None),
) -> AuthenticatedUser:
    """FastAPI dependency: returns the verified OWNER identity or raises 401/403."""
    cfg = request.app.state.settings
    token = _extract_bearer(authorization)

    if _peek_issuer(token) == LOCAL_ISSUER:
        if not cfg.local_auth_enabled:
            raise _unauthorized("Invalid or unsupported token")
        claims = verify_hs256_jwt(token, cfg.auth_jwt_secret)
        if claims.get("iss") != LOCAL_ISSUER or not _aud_contains(claims.get("aud"), LOCAL_AUDIENCE):
            raise _unauthorized("Invalid token issuer or audience")
        method = "owner_session"
    else:
        if not cfg.supabase_auth_enabled:
            raise _unauthorized("Invalid or unsupported token")
        claims = verify_hs256_jwt(token, cfg.supabase_jwt_secret)
        if not _aud_contains(claims.get("aud"), SUPABASE_AUDIENCE) or claims.get("role") != "authenticated":
            raise _unauthorized("Token is not a Supabase user access token")
        method = "supabase"

    subject = str(claims["sub"])
    if not hmac.compare_digest(subject.encode("utf-8"), cfg.owner_id.encode("utf-8")):
        logger.warning("Rejected valid token for non-owner identity", extra={"structured_data": {"auth_method": method}})
        raise TradeIQException("FORBIDDEN", "This deployment is single-user; access is restricted to the owner", status_code=403)

    return AuthenticatedUser(
        user_id=cfg.owner_id,
        email=str(claims.get("email") or ""),
        role=str(claims.get("role") or "authenticated"),
        auth_method=method,
    )


class LoginThrottle:
    """
    In-memory brute-force protection for the owner login.
    Per-client lockout (default 5 failures / 15 min) plus a global ceiling (20 failures / 15 min).
    Process-local: correct for the current single-worker deployment (all app state is in-memory too).
    """

    _MAX_KEYS = 2048

    def __init__(self, max_per_key: int = 5, max_global: int = 20, window_sec: int = 900, clock=time.monotonic) -> None:
        self.max_per_key = max_per_key
        self.max_global = max_global
        self.window_sec = window_sec
        self._clock = clock
        self._per_key: Dict[str, Deque[float]] = {}
        self._global: Deque[float] = collections.deque()

    def _prune(self, dq: Deque[float], now: float) -> None:
        while dq and now - dq[0] >= self.window_sec:
            dq.popleft()

    def _retry(self, dq: Deque[float], limit: int, now: float) -> int:
        self._prune(dq, now)
        if len(dq) >= limit:
            return max(1, int(self.window_sec - (now - dq[len(dq) - limit])) + 1)
        return 0

    def retry_after(self, key: str) -> int:
        """Seconds until a login attempt is allowed again (0 = allowed now)."""
        now = self._clock()
        wait_key = self._retry(self._per_key.get(key, collections.deque()), self.max_per_key, now)
        wait_global = self._retry(self._global, self.max_global, now)
        return max(wait_key, wait_global)

    def record_failure(self, key: str) -> None:
        now = self._clock()
        if len(self._per_key) >= self._MAX_KEYS:
            for k in [k for k, dq in self._per_key.items() if not dq or now - dq[-1] >= self.window_sec]:
                del self._per_key[k]
            if len(self._per_key) >= self._MAX_KEYS:
                self._per_key.pop(next(iter(self._per_key)))
        self._per_key.setdefault(key, collections.deque()).append(now)
        self._global.append(now)

    def record_success(self, key: str) -> None:
        self._per_key.pop(key, None)
