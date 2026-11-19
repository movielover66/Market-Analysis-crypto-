"""
Section 5 & Section 8 — Typed Pydantic Request/Response Contracts (`backend/app/schemas/contracts.py`).
"""
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class ProvenanceSchema(BaseModel):
    source: str
    exchange_ts_ist: str
    ingested_ts_utc: str
    freshness: str
    quality_status: str
    is_simulated: bool


class QuoteSchema(BaseModel):
    instrument_id: str
    symbol: str
    exchange: str = "NSE"
    ltp: str
    prev_close: str
    open: str
    high: str
    low: str
    volume: int
    provenance: ProvenanceSchema


class CandleSchema(BaseModel):
    instrument_id: str
    symbol: str
    timeframe: str
    start_ts_ist: str
    open: str
    high: str
    low: str
    close: str
    volume: int
    is_closed: bool
    adjustment_status: str
    provenance: ProvenanceSchema


class GroundedAIResponseSchema(BaseModel):
    data_used: str
    analysis: str
    evidence: List[str]
    cited_fact_ids: List[str]
    risks_and_limitations: str
    conclusion: str
    plan_status: str
    timestamp: str
    source: str = Field(..., description="GEMINI_VALIDATED or DETERMINISTIC_TEMPLATE")


class JobTriggerRequest(BaseModel):
    job_name: str = Field(..., description="refresh_instruments | ingest_candles | expire_plans | evaluate_alerts")
    symbols: List[str] = ["RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "SBIN"]
