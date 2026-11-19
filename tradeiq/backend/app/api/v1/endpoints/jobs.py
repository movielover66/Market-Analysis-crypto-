"""
Scheduled-job trigger. Authenticated by the machine credential INTERNAL_JOB_TOKEN
(constant-time compare; an unset token rejects every request) — NOT by the owner session.
"""
from dataclasses import asdict
from typing import Optional
from fastapi import APIRouter, Header, HTTPException, Request

from ....schemas.contracts import JobTriggerRequest

router = APIRouter(tags=["jobs"])


@router.post("/jobs/trigger")
async def api_trigger_protected_job(
    payload: JobTriggerRequest,
    request: Request,
    x_internal_job_token: Optional[str] = Header(None),
):
    runner = request.app.state.job_runner
    if not runner.verify_job_token(x_internal_job_token):
        raise HTTPException(status_code=401, detail="Invalid or missing X-Internal-Job-Token")
    rec = runner.run_job(payload.job_name, {"symbols": payload.symbols})
    return {"job": asdict(rec)}
