"""
Milestone 2.6 — Canonical NSE Cash Equity Instrument Universe Service.
Resolves symbols to canonical IDs, tracks ACTIVE / SUSPENDED / UNAVAILABLE status,
and supports search & watchlist resolution.
"""
from decimal import Decimal
from typing import Dict, List, Optional
from ..providers.base import NormalizedInstrument

SEED_NSE_UNIVERSE = [
    ("RELIANCE", "Reliance Industries Ltd", "2885", "Energy"),
    ("TCS", "Tata Consultancy Services Ltd", "11536", "IT"),
    ("HDFCBANK", "HDFC Bank Ltd", "1333", "Banking"),
    ("ICICIBANK", "ICICI Bank Ltd", "4963", "Banking"),
    ("SBIN", "State Bank of India", "3045", "Banking"),
    ("INFY", "Infosys Ltd", "1594", "IT"),
    ("BHARTIARTL", "Bharti Airtel Ltd", "10604", "Telecom"),
    ("ITC", "ITC Ltd", "1660", "FMCG"),
    ("LT", "Larsen & Toubro Ltd", "11483", "Infrastructure"),
    ("AXISBANK", "Axis Bank Ltd", "5900", "Banking"),
    ("BAJFINANCE", "Bajaj Finance Ltd", "317", "Financials"),
    ("SUNPHARMA", "Sun Pharmaceutical Industries Ltd", "3351", "Pharma"),
    ("MARUTI", "Maruti Suzuki India Ltd", "10999", "Auto"),
    ("TATAMOTORS", "Tata Motors Ltd", "3456", "Auto"),
    ("TITAN", "Titan Company Ltd", "3506", "Consumer"),
    ("TRENT", "Trent Ltd", "1964", "Retail"),
    ("HAL", "Hindustan Aeronautics Ltd", "2303", "Defence"),
    ("BEL", "Bharat Electronics Ltd", "383", "Defence"),
]


class InstrumentService:
    def __init__(self, source_label: str = "DEMO") -> None:
        self._by_symbol: Dict[str, NormalizedInstrument] = {}
        self._by_id: Dict[str, NormalizedInstrument] = {}
        for sym, name, token, sector in SEED_NSE_UNIVERSE:
            inst = NormalizedInstrument(
                instrument_id=f"nse-{sym.lower()}",
                exchange="NSE",
                segment="CASH_EQUITY",
                symbol=sym,
                name=name,
                provider_token=token,
                tick_size=Decimal("0.0500"),
                lot_size=1,
                sector=sector,
                status="ACTIVE",
                source=source_label,
            )
            self._by_symbol[sym] = inst
            self._by_id[inst.instrument_id] = inst

    def replace_with_provider_instruments(self, instruments: List[NormalizedInstrument]) -> None:
        """Replace seeded/demo symbols with the verified broker instrument master."""
        if not instruments:
            return
        self._by_symbol = {i.symbol.upper(): i for i in instruments}
        self._by_id = {i.instrument_id: i for i in instruments}

    def resolve_symbol(self, symbol: str) -> Optional[NormalizedInstrument]:
        s = symbol.strip().upper().replace(".NS", "").replace("-EQ", "")
        return self._by_symbol.get(s)

    def set_status(self, symbol: str, status: str) -> Optional[NormalizedInstrument]:
        inst = self.resolve_symbol(symbol)
        if not inst:
            return None
        updated = NormalizedInstrument(
            instrument_id=inst.instrument_id,
            exchange=inst.exchange,
            segment=inst.segment,
            symbol=inst.symbol,
            name=inst.name,
            provider_token=inst.provider_token,
            tick_size=inst.tick_size,
            lot_size=inst.lot_size,
            sector=inst.sector,
            status=status,
            source=inst.source,
        )
        self._by_symbol[inst.symbol] = updated
        self._by_id[inst.instrument_id] = updated
        return updated

    def search(self, query: str = "", active_only: bool = False) -> List[NormalizedInstrument]:
        q = query.strip().upper()
        out = []
        for inst in self._by_symbol.values():
            if active_only and inst.status != "ACTIVE":
                continue
            if not q or q in inst.symbol or q in inst.name.upper() or q in inst.sector.upper():
                out.append(inst)
        return sorted(out, key=lambda x: x.symbol)
