"""
Standardized machine-readable error responses and exception handlers for TRADEIQ.
"""
from typing import Any, Dict, Optional
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .logging import configure_logging, request_id_ctx, scrub_secrets

logger = configure_logging()


class TradeIQException(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 400,
        details: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}
        self.headers = headers or {}


def error_envelope(code: str, message: str, details: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "details": scrub_secrets(details or {}),
            "request_id": request_id_ctx.get("-"),
        }
    }


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(TradeIQException)
    async def handle_tradeiq_exception(_request: Request, exc: TradeIQException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=error_envelope(exc.code, exc.message, exc.details),
            headers={**exc.headers, "X-Request-ID": request_id_ctx.get("-")},
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code_map = {
            401: "UNAUTHORIZED",
            403: "FORBIDDEN",
            404: "NOT_FOUND",
            422: "VALIDATION_ERROR",
            429: "RATE_LIMITED",
        }
        code = code_map.get(exc.status_code, "HTTP_ERROR")
        return JSONResponse(
            status_code=exc.status_code,
            content=error_envelope(code, str(exc.detail)),
            headers={"X-Request-ID": request_id_ctx.get("-")},
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=error_envelope(
                "VALIDATION_ERROR",
                "Request validation failed",
                {"errors": exc.errors()},
            ),
            headers={"X-Request-ID": request_id_ctx.get("-")},
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_exception(_request: Request, exc: Exception) -> JSONResponse:
        logger.error("Unhandled server error", exc_info=exc)
        return JSONResponse(
            status_code=500,
            content=error_envelope(
                "INTERNAL_SERVER_ERROR",
                "An unexpected internal error occurred.",
            ),
            headers={"X-Request-ID": request_id_ctx.get("-")},
        )
