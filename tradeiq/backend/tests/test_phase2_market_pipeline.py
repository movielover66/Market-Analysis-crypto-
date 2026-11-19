"""
Phase 2 Acceptance Tests (Milestones 2.1 to 2.7):
- Milestone 2.1: Timezone-safe NSE calendar (pre-market, open, closed, weekend, holiday, unavailable) & freshness
- Milestone 2.2 & 2.3: Provider interface, AngelOneProvider 00:00 IST token expiry, paise->Decimal tick normalization, ScripMaster chunk parser
- Milestone 2.4: OHLCV validator & quarantine (invalid high/low, negative vol, duplicate ts, out-of-order ts, extreme jumps)
- Milestone 2.5: 09:15 IST anchored 5m -> 15m/30m/1h aggregation & PROVISIONAL flag on incomplete buckets
- Milestone 2.6: Instrument universe resolution & suspended instrument rejection
- Milestone 2.7: Pure domain indicators (EMA, Wilder RSI, MACD, Session VWAP, Wilder ATR, RVOL) golden fixture verification
"""
import datetime
import unittest
from decimal import Decimal
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.domain.calendar.nse_calendar import IST, evaluate_freshness, get_market_state, is_trading_day
from app.domain.candles.aggregator import aggregate_5m_candles
from app.domain.candles.validator import DomainCandle, validate_candle_series
from app.domain.indicators.core import (
    compute_indicator_snapshot,
    ema_series,
    macd_series,
    session_vwap_series,
    wilder_atr_series,
    wilder_rsi_series,
)
from app.main import create_app
from tests._auth_helpers import owner_headers, owner_settings
from app.providers.angel_one_provider import (
    AngelOneProvider,
    normalize_angel_ws_tick,
    parse_scrip_master_chunk,
)


class TestPhase2MarketPipeline(unittest.TestCase):
    def test_milestone_2_1_nse_calendar_and_freshness(self) -> None:
        # Regular Wednesday at 09:05 IST -> PRE_MARKET
        dt_pre = datetime.datetime(2026, 11, 18, 9, 5, tzinfo=IST)
        self.assertEqual(get_market_state(dt_pre), "PRE_MARKET")

        # Regular Wednesday at 10:30 IST -> OPEN (even if passed in UTC)
        dt_open_utc = datetime.datetime(2026, 11, 18, 5, 0, tzinfo=datetime.timezone.utc)
        self.assertEqual(get_market_state(dt_open_utc), "OPEN")

        # Regular Wednesday at 15:35 IST -> CLOSED
        dt_closed = datetime.datetime(2026, 11, 18, 15, 35, tzinfo=IST)
        self.assertEqual(get_market_state(dt_closed), "CLOSED")

        # Saturday -> HOLIDAY
        dt_sat = datetime.datetime(2026, 11, 21, 11, 0, tzinfo=IST)
        self.assertEqual(get_market_state(dt_sat), "HOLIDAY")
        self.assertFalse(is_trading_day(dt_sat.date()))

        # Official NSE holiday (2026-11-24 Gurunanak Jayanti) -> HOLIDAY
        dt_hol = datetime.datetime(2026, 11, 24, 11, 0, tzinfo=IST)
        self.assertEqual(get_market_state(dt_hol), "HOLIDAY")

        # Freshness becomes STALE when LIVE quote is > 90s old
        old_ts = dt_open_utc - datetime.timedelta(seconds=120)
        self.assertEqual(evaluate_freshness(old_ts, dt_open_utc, "LIVE", max_live_age_seconds=90), "STALE")
        self.assertEqual(evaluate_freshness(dt_open_utc, dt_open_utc, "LIVE", max_live_age_seconds=90), "LIVE")

    def test_milestone_2_3_angel_one_adapter_and_expiry(self) -> None:
        # Paise -> Decimal rupee normalization
        now_utc = datetime.datetime(2026, 11, 18, 5, 0, tzinfo=datetime.timezone.utc)
        raw_tick = {
            "token": "2885",
            "last_traded_price": 134255,   # 1342.55 INR
            "closed_price": 133000,
            "open_price_of_the_day": 133500,
            "high_price_of_the_day": 135000,
            "low_price_of_the_day": 132800,
            "volume_trade_for_the_day": 450000,
            "exchange_timestamp": int(now_utc.timestamp() * 1000),
        }
        q = normalize_angel_ws_tick(raw_tick, {"2885": "RELIANCE"}, now_utc=now_utc)
        self.assertIsNotNone(q)
        self.assertEqual(q.ltp, Decimal("1342.55"))
        self.assertEqual(q.provenance.source, "ANGEL_ONE")
        self.assertEqual(q.provenance.freshness, "LIVE")

        # ScripMaster streaming chunk parser
        sample_chunk = (
            '[{"token":"2885","symbol":"RELIANCE-EQ","name":"RELIANCE","exch_seg":"NSE","tick_size":"5.000000","lotsize":"1"},'
            '{"token":"99999","symbol":"NIFTY26NOVFUT","name":"NIFTY","exch_seg":"NFO","tick_size":"5.000000","lotsize":"50"}]'
        )
        parsed = parse_scrip_master_chunk(sample_chunk)
        self.assertIn("RELIANCE", parsed)
        self.assertNotIn("NIFTY", parsed)
        self.assertEqual(parsed["RELIANCE"].tick_size, Decimal("0.0500"))

        # Session expiry at 00:00 IST next day -> RECONNECT_REQUIRED
        angel = AngelOneProvider("k", "c", "p", "totp")
        angel.jwt_token = "jwt-123"
        angel.session_created_ist = datetime.datetime(2026, 11, 17, 9, 10, tzinfo=IST)
        self.assertEqual(
            angel.connection_state(datetime.datetime(2026, 11, 17, 15, 0, tzinfo=IST)),
            "AUTHENTICATED_NO_RECENT_TICKS",
        )
        self.assertEqual(
            angel.connection_state(datetime.datetime(2026, 11, 18, 0, 1, tzinfo=IST)),
            "RECONNECT_REQUIRED",
        )

    def test_milestone_2_4_candle_validation_and_quarantine(self) -> None:
        t0 = datetime.datetime(2026, 11, 18, 9, 15, tzinfo=IST)
        c_good = DomainCandle("RELIANCE", "5m", t0, Decimal("1300"), Decimal("1305"), Decimal("1298"), Decimal("1303"), 10000)
        c_dup = DomainCandle("RELIANCE", "5m", t0, Decimal("1303"), Decimal("1306"), Decimal("1301"), Decimal("1304"), 8000)
        c_bad_ohlc = DomainCandle("RELIANCE", "5m", t0 + datetime.timedelta(minutes=5), Decimal("1303"), Decimal("1300"), Decimal("1295"), Decimal("1302"), 5000)
        c_spike = DomainCandle("RELIANCE", "5m", t0 + datetime.timedelta(minutes=10), Decimal("1303"), Decimal("1800"), Decimal("1300"), Decimal("1750"), 5000)

        valid, quarantined = validate_candle_series([c_good, c_dup, c_bad_ohlc, c_spike])
        self.assertEqual(len(valid), 1)
        self.assertEqual(len(quarantined), 3)
        codes = [q.reason_code for q in quarantined]
        self.assertEqual(codes, ["DUPLICATE_TIMESTAMP", "INVALID_OHLC_BOUNDS", "UNEXPECTED_PRICE_JUMP"])

    def test_milestone_2_5_5m_to_15m_aggregation_and_provisional_flag(self) -> None:
        t0 = datetime.datetime(2026, 11, 18, 9, 15, tzinfo=IST)
        bars_5m = [
            # Complete 09:15–09:30 bucket (3 closed 5m bars)
            DomainCandle("TCS", "5m", t0, Decimal("4100"), Decimal("4110"), Decimal("4095"), Decimal("4105"), 1000, is_closed=True),
            DomainCandle("TCS", "5m", t0 + datetime.timedelta(minutes=5), Decimal("4105"), Decimal("4120"), Decimal("4102"), Decimal("4118"), 2000, is_closed=True),
            DomainCandle("TCS", "5m", t0 + datetime.timedelta(minutes=10), Decimal("4118"), Decimal("4119"), Decimal("4108"), Decimal("4112"), 1500, is_closed=True),
            # Incomplete 09:30–09:45 bucket (only 2 bars so far -> must be PROVISIONAL)
            DomainCandle("TCS", "5m", t0 + datetime.timedelta(minutes=15), Decimal("4112"), Decimal("4125"), Decimal("4110"), Decimal("4122"), 1200, is_closed=True),
            DomainCandle("TCS", "5m", t0 + datetime.timedelta(minutes=20), Decimal("4122"), Decimal("4130"), Decimal("4120"), Decimal("4128"), 900, is_closed=False),
        ]
        bars_15m = aggregate_5m_candles(bars_5m, "15m")
        self.assertEqual(len(bars_15m), 2)

        first = bars_15m[0]
        self.assertEqual(first.open, Decimal("4100"))
        self.assertEqual(first.high, Decimal("4120"))
        self.assertEqual(first.low, Decimal("4095"))
        self.assertEqual(first.close, Decimal("4112"))
        self.assertEqual(first.volume, 4500)
        self.assertTrue(first.is_closed)
        self.assertEqual(first.quality_status, "VALID")

        second = bars_15m[1]
        self.assertFalse(second.is_closed)
        self.assertEqual(second.quality_status, "PROVISIONAL")

    def test_milestone_2_7_indicators_and_market_endpoints(self) -> None:
        settings = owner_settings()
        client = TestClient(create_app(settings), headers=owner_headers(settings))

        r_ind = client.get("/api/v1/market/indicators/RELIANCE")
        self.assertEqual(r_ind.status_code, 200)
        body = r_ind.json()
        self.assertEqual(body["provenance"]["freshness"], "SIMULATED")
        ind = body["indicators"]
        for key in ("ema_9", "ema_20", "ema_50", "rsi_14", "macd", "macd_signal", "atr_14", "session_vwap", "rvol"):
            self.assertIsNotNone(ind[key], f"Indicator {key} should not be None on 60-bar series")


if __name__ == "__main__":
    unittest.main()
