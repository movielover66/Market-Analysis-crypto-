from fastapi import APIRouter, Depends
from ...core.auth import get_current_user
from .endpoints import auth, demo, intelligence, jobs, market, system

api_v1_router = APIRouter(prefix="/api/v1")

# --- PUBLIC (deliberately minimal) -------------------------------------------------------
api_v1_router.include_router(system.public_router)   # GET  /health        (liveness for Render)
api_v1_router.include_router(auth.public_router)     # POST /auth/login    (throttled)
api_v1_router.include_router(jobs.router)            # POST /jobs/trigger  (X-Internal-Job-Token)

# --- PRIVATE: secure by default ----------------------------------------------------------
# Owner authentication is attached at router level, so any endpoint added to these routers is
# protected automatically; forgetting a per-endpoint Depends cannot expose it.
_owner_only = [Depends(get_current_user)]
api_v1_router.include_router(system.router, dependencies=_owner_only)
api_v1_router.include_router(auth.router, dependencies=_owner_only)
api_v1_router.include_router(demo.router, dependencies=_owner_only)
api_v1_router.include_router(market.router, dependencies=_owner_only)
api_v1_router.include_router(intelligence.router, dependencies=_owner_only)
