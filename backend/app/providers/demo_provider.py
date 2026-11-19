"""
Milestone 1.6 — Explicit DemoMarketDataProvider.
All quotes and candles are tagged source="DEMO", freshness="SIMULATED", is_simulated=True.
Cannot be used when DEMO_MODE is False.
"""
import datetime
from decimal import Decimal
from typing import Dict, List, Optional

from ..core.errors import TradeIQException
from .base import (
    MarketDataProvider,
    NormalizedCandle,
    NormalizedInstrument,
    NormalizedQuote,
    ProvenanceEnvelope,
)

_IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

DEMO_INSTRUMENTS: List[NormalizedInstrument] = [
    NormalizedInstrument("demo-rel", "NSE", "CASH_EQUITY", "RELIANCE", "Reliance Industries Ltd", "2885", Decimal("0.05"), 1, "Energy", "ACTIVE", "DEMO"),
    NormalizedInstrument("demo-tcs", "NSE", "CASH_EQUITY", "TCS", "Tata Consultancy Services Ltd", "11536", Decimal("0.05"), 1, "IT", "ACTIVE", "DEMO"),
    NormalizedInstrument("demo-hdfc", "NSE", "CASH_EQUITY", "HDFCBANK", "HDFC Bank Ltd", "1333", Decimal("0.05"), 1, "Banking", "ACTIVE", "DEMO"),
    NormalizedInstrument("demo-icici", "NSE", "CASH_EQUITY", "ICICIBANK", "ICICI Bank Ltd", "4963", Decimal("0.05"), 1, "Banking", "ACTIVE", "DEMO"),
    NormalizedInstrument("demo-sbin", "NSE", "CASH_EQUITY", "SBIN", "State Bank of India", "3045", Decimal("0.05"), 1, "Banking", "ACTIVE", "DEMO"),
    NormalizedInstrument("demo-infy", "NSE", "CASH_EQUITY", "INFY", "Infosys Ltd", "1594", Decimal("0.05"), 1, "IT", "ACTIVE", "DEMO"),
]

_BASE_PRICES: Dict[str, Decimal] = {
    "RELIANCE": Decimal("1342.50"),
    "TCS": Decimal("4125.00"),
    "HDFCBANK": Decimal("1748.25"),
    "ICICIBANK": Decimal("1286.40"),
    "SBIN": Decimal("824.60"),
    "INFY": Decimal("1865.15"),
}


class DemoMarketDataProvider(MarketDataProvider):
    def __init__(self, demo_mode_enabled: bool = True) -> None:
        self.demo_mode_enabled = demo_mode_enabled

    def _assert_demo_allowed(self) -> None:
        if not self.demo_mode_enabled:
            raise TradeIQException(
                code="DEMO_MODE_DISABLED",
                message="Demo/Simulated market data is disabled when DEMO_MODE=false.",
                status_code=403,
            )

    def _sim_provenance(self) -> ProvenanceEnvelope:
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        now_ist = now_utc.astimezone(_IST)
        return ProvenanceEnvelope(
            source="DEMO",
            exchange_ts_ist=now_ist.isoformat(),
            ingested_ts_utc=now_utc.isoformat(),
            freshness="SIMULATED",
            quality_status="VALID",
            is_simulated=True,
        )

    def get_instruments(self) -> List[NormalizedInstrument]:
        self._assert_demo_allowed()
        return list(DEMO_INSTRUMENTS)

    def get_quote(self, symbol: str) -> Optional[NormalizedQuote]:
        self._assert_demo_allowed()
        sym = symbol.strip().upper()
        inst = next((i for i in DEMO_INSTRUMENTS if i.symbol == sym), None)
        if not inst:
            return None
        base = _BASE_PRICES[sym]
        return NormalizedQuote(
            instrument_id=inst.instrument_id,
            symbol=inst.symbol,
            exchange="NSE",
            ltp=base,
            prev_close=(base - Decimal("14.50")).quantize(Decimal("0.05")),
            open=(base - Decimal("10.00")).quantize(Decimal("0.05")),
            high=(base + Decimal("8.25")).quantize(Decimal("0.05")),
            low=(base - Decimal("16.00")).quantize(Decimal("0.05")),
            volume=1845200,
            provenance=self._sim_provenance(),
        )

    def get_candles(self, symbol: str, timeframe: str = "1d", limit: int = 30) -> List[NormalizedCandle]:
        self._assert_demo_allowed()
        sym = symbol.strip().upper()
        inst = next((i for i in DEMO_INSTRUMENTS if i.symbol == sym), None)
        if not inst:
            return []
        base = _BASE_PRICES[sym]
        prov = self._sim_provenance()
        candles: List[NormalizedCandle] = []
        base_dt = datetime.datetime(2026, 11, 17, 9, 15, tzinfo=_IST)
        for idx in range(limit):
            step = Decimal(str((idx - limit // 2) * 2.5))
            o = (base + step).quantize(Decimal("0.05"))
            c = (o + Decimal("3.25")).quantize(Decimal("0.05"))
            h = (max(o, c) + Decimal("4.50")).quantize(Decimal("0.05"))
            l = (min(o, c) - Decimal("3.00")).quantize(Decimal("0.05"))
            ts = (base_dt - datetime.timedelta(days=(limit - 1 - idx))).isoformat()
            candles.append(
                NormalizedCandle(
                    instrument_id=inst.instrument_id,
                    symbol=sym,
                    timeframe=timeframe,
                    start_ts_ist=ts,
                    open=o,
                    high=h,
                    low=l,
                    close=c,
                    volume=950000 + idx * 15000,
                    is_closed=True,
                    adjustment_status="ADJUSTED",
                    provenance=prov,
                )
            )
        return candles

    def get_index_quotes(self) -> list:
        return [
            {"symbol": "NIFTY 50", "exchange": "NSE", "ltp": "24685.50", "prev_close": "24520.00", "change_inr": "165.50", "change_pct": "0.67", "freshness": "SIMULATED", "source": "DEMO"},
            {"symbol": "BANKNIFTY", "exchange": "NSE", "ltp": "52410.25", "prev_close": "52080.00", "change_inr": "330.25", "change_pct": "0.63", "freshness": "SIMULATED", "source": "DEMO"},
            {"symbol": "SENSEX", "exchange": "BSE", "ltp": "81140.80", "prev_close": "80610.00", "change_inr": "530.80", "change_pct": "0.66", "freshness": "SIMULATED", "source": "DEMO"},
        ]

    def get_market_depth(self, symbol: str):
        q = self.get_quote(symbol)
        if not q:
            return None
        ltp = q.ltp
        return {
            "symbol": symbol.upper(),
            "source": "DEMO_DEPTH",
            "freshness": "SIMULATED",
            "total_buy_qty": 148200,
            "total_sell_qty": 98400,
            "best_5_buy": [{"price": str(ltp - (Decimal("0.05") * Decimal(str(i + 1))).quantize(Decimal("0.05"))), "quantity": 1200 * (5 - i), "orders": 14 - i} for i in range(5)],
            "best_5_sell": [{"price": str(ltp + (Decimal("0.05") * Decimal(str(i + 1))).quantize(Decimal("0.05"))), "quantity": 850 * (5 - i), "orders": 10 - i} for i in range(5)],
        }
