"""
Request ID propagation and safe structured request logging middleware.
"""
import re
import time
import uuid
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from .logging import configure_logging, request_id_ctx

logger = configure_logging()
_SAFE_REQ_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{4,64}$")


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        incoming_id = (request.headers.get("X-Request-ID") or "").strip()
        req_id = incoming_id if _SAFE_REQ_ID_RE.match(incoming_id) else uuid.uuid4().hex[:16]
        token = request_id_ctx.set(req_id)
        start = time.perf_counter()
        try:
            response: Response = await call_next(request)
            elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
            response.headers["X-Request-ID"] = req_id
            logger.info(
                f"{request.method} {request.url.path} -> {response.status_code} ({elapsed_ms}ms)",
                extra={"structured_data": {"method": request.method, "path": request.url.path, "status": response.status_code, "ms": elapsed_ms}},
            )
            return response
        finally:
            request_id_ctx.reset(token)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Baseline response hardening. API responses (tokens, trading data) are never cacheable."""

    async def dispatch(self, request: Request, call_next) -> Response:
        response: Response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
            response.headers.setdefault("Pragma", "no-cache")
        return response
