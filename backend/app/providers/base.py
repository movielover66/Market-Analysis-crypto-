"""
MarketDataProvider Interface & Provenance Envelope Models (Section 7 & Section 9).
Every value carries source, exchange timestamp in IST, ingestion timestamp, and freshness class.
Uses Decimal for all prices (Rule 6).
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from typing import List, Optional

FRESHNESS_CLASSES = ("LIVE", "DELAYED", "HISTORICAL", "SIMULATED", "STALE", "UNAVAILABLE")
QUALITY_STATUSES = ("VALID", "QUARANTINED", "PROVISIONAL")


@dataclass(frozen=True)
class ProvenanceEnvelope:
    source: str
    exchange_ts_ist: str
    ingested_ts_utc: str
    freshness: str
    quality_status: str = "VALID"
    is_simulated: bool = False


@dataclass(frozen=True)
class NormalizedInstrument:
    instrument_id: str
    exchange: str
    segment: str
    symbol: str
    name: str
    provider_token: str
    tick_size: Decimal
    lot_size: int
    sector: str
    status: str
    source: str


@dataclass(frozen=True)
class NormalizedQuote:
    instrument_id: str
    symbol: str
    exchange: str
    ltp: Decimal
    prev_close: Decimal
    open: Decimal
    high: Decimal
    low: Decimal
    volume: int
    provenance: ProvenanceEnvelope


@dataclass(frozen=True)
class NormalizedCandle:
    instrument_id: str
    symbol: str
    timeframe: str
    start_ts_ist: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    is_closed: bool
    adjustment_status: str
    provenance: ProvenanceEnvelope


class MarketDataProvider(ABC):
    @abstractmethod
    def get_instruments(self) -> List[NormalizedInstrument]:
        raise NotImplementedError

    @abstractmethod
    def get_quote(self, symbol: str) -> Optional[NormalizedQuote]:
        raise NotImplementedError

    @abstractmethod
    def get_candles(self, symbol: str, timeframe: str, limit: int = 30) -> List[NormalizedCandle]:
        raise NotImplementedError
