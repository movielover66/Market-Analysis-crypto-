from dataclasses import asdict
from typing import List, Optional
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from ....core.auth import AuthenticatedUser, get_current_user, issue_owner_token
from ....core.errors import TradeIQException
from ....core.logging import configure_logging
from ....core.password_hash import verify_password

logger = configure_logging()

# Only the login route is reachable without a token. Everything on `router` is mounted behind
# the owner-auth dependency in api/v1/router.py (and also declares it per-endpoint where the user is needed).
public_router = APIRouter(tags=["auth"])
router = APIRouter(tags=["auth-and-user"])


class LoginRequest(BaseModel):
    password: str = Field(..., min_length=1, max_length=256)


@public_router.post("/auth/login")
async def owner_login(payload: LoginRequest, request: Request):
    """Single-owner login. Returns a short-lived bearer token. Failed attempts are throttled."""
    cfg = request.app.state.settings
    if not cfg.local_auth_enabled:
        raise TradeIQException("AUTH_NOT_CONFIGURED", "Owner login is not configured on this server", status_code=503)

    throttle = request.app.state.login_throttle
    client_key = request.client.host if request.client else "unknown"
    wait = throttle.retry_after(client_key)
    if wait:
        logger.warning("Owner login throttled", extra={"structured_data": {"client": client_key, "retry_after": wait}})
        raise TradeIQException(
            "RATE_LIMITED", "Too many failed login attempts. Try again later.",
            status_code=429, headers={"Retry-After": str(wait)},
        )

    if not verify_password(payload.password, cfg.owner_password_hash):
        throttle.record_failure(client_key)
        logger.warning("Owner login failed", extra={"structured_data": {"client": client_key}})
        raise TradeIQException(
            "UNAUTHORIZED", "Invalid credentials", status_code=401, headers={"WWW-Authenticate": "Bearer"},
        )

    throttle.record_success(client_key)
    token, expires_in = issue_owner_token(cfg)
    logger.info("Owner login succeeded", extra={"structured_data": {"client": client_key}})
    return {"access_token": token, "token_type": "bearer", "expires_in": expires_in}


class SettingsUpdateRequest(BaseModel):
    capital_inr: Optional[float] = None
    risk_per_trade_pct: Optional[float] = None
    daily_loss_limit_pct: Optional[float] = None
    max_open_positions: Optional[int] = None
    max_single_position_exposure_pct: Optional[float] = None
    minimum_net_rr: Optional[float] = None
    theme: Optional[str] = None


class WatchlistCreateRequest(BaseModel):
    name: str
    symbols: List[str] = []


@router.get("/auth/me")
async def get_me(user: AuthenticatedUser = Depends(get_current_user)):
    return {
        "user": asdict(user),
        "authenticated": True,
    }


@router.get("/settings")
async def get_user_settings(
    request: Request,
    target_user_id: Optional[str] = None,
    user: AuthenticatedUser = Depends(get_current_user),
):
    repo = request.app.state.user_repo
    return {"settings": repo.get_settings(user.user_id, target_user_id)}


@router.put("/settings")
async def update_user_settings(
    payload: SettingsUpdateRequest,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
):
    repo = request.app.state.user_repo
    updates = {k: v for k, v in payload.dict().items() if v is not None}
    return {"settings": repo.update_settings(user.user_id, updates)}


@router.get("/watchlists")
async def list_user_watchlists(
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
):
    repo = request.app.state.user_repo
    return {"watchlists": repo.list_watchlists(user.user_id)}


@router.post("/watchlists", status_code=201)
async def create_user_watchlist(
    payload: WatchlistCreateRequest,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
):
    repo = request.app.state.user_repo
    return {"watchlist": repo.create_watchlist(user.user_id, payload.name, payload.symbols)}


@router.get("/watchlists/{watchlist_id}")
async def get_user_watchlist(
    watchlist_id: str,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
):
    repo = request.app.state.user_repo
    return {"watchlist": repo.get_watchlist(user.user_id, watchlist_id)}
