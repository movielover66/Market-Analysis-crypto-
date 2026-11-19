import io
import json
import unittest
from unittest.mock import patch

from app.jobs.cron_runner import TelegramNotifier
from app.providers.angel_one_provider import AngelOneProvider, normalize_angel_ws_tick
from app.repositories.supabase_bridge import SupabaseRestBridge
from app.repositories.legacy_paper_trade_store import LegacyPaperTradeStore
from app.domain.paper.simulator import PaperPosition
from decimal import Decimal


class FakeResponse:
    status = 200
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self): return json.dumps({"ok": True, "result": {"message_id": 7}}).encode()


class TestLegacyIntegrationAdapters(unittest.TestCase):
    def test_angel_provider_requires_full_credentials_and_no_false_live(self):
        provider = AngelOneProvider("key", "client", "pin", "totp")
        self.assertTrue(provider.is_configured())
        self.assertEqual(provider.connection_state(), "DISCONNECTED")
        self.assertFalse(provider.get_candles("RELIANCE", "1d", 10))

    def test_tick_normalization_uses_provider_token_map(self):
        q = normalize_angel_ws_tick(
            {"token": "2885", "last_traded_price": 123450, "exchange_timestamp": 1794978000000},
            {"2885": "RELIANCE"},
        )
        self.assertIsNotNone(q)
        self.assertEqual(q.symbol, "RELIANCE")
        self.assertEqual(q.provenance.source, "ANGEL_ONE")

    def test_telegram_live_http_delivery_is_confirmed_from_api_body(self):
        notifier = TelegramNotifier("123:token", "456", live_http=True)
        with patch("urllib.request.urlopen", return_value=FakeResponse()):
            result = notifier.send_closed_candle_alert("RELIANCE", "Test", "2026-11-18T10:00:00+05:30")
        self.assertTrue(result["delivered"])
        self.assertEqual(notifier.status_summary()["delivery_mode"], "LIVE_HTTP")


    def test_legacy_paper_store_insert_uses_previous_table_contract(self):
        store = LegacyPaperTradeStore("https://example.supabase.co", "service-key")
        position = PaperPosition(
            position_id="pos-123", user_id="demo-user", plan_id=None, symbol="RELIANCE",
            trading_mode="SWING", direction="LONG", status="OPEN", quantity=2,
            planned_entry=Decimal("100"), entry_fill_price=Decimal("100.05"),
            stop_loss=Decimal("95"), target_price=Decimal("110"),
        )
        with patch.object(store, "_request", return_value=[{"id": "pos-123"}]) as request:
            result = store.insert_position(position)
        self.assertTrue(result["persisted"])
        self.assertEqual(request.call_args.args[1], "paper_trades")
        self.assertEqual(request.call_args.args[2]["market"], "NSE")
        self.assertEqual(request.call_args.args[2]["status"], "HOLDING")

    def test_supabase_config_is_not_reported_verified_until_checked(self):
        bridge = SupabaseRestBridge("https://example.supabase.co", "test-key")
        self.assertEqual(bridge.status_summary()["mode"], "CONFIGURED_NOT_VERIFIED")
        with patch("urllib.request.urlopen", return_value=FakeResponse()):
            self.assertTrue(bridge.verify_connection())
        self.assertEqual(bridge.status_summary()["mode"], "CONNECTED")


if __name__ == "__main__":
    unittest.main()
