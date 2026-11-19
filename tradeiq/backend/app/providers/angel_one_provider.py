"""Angel One SmartAPI read-only NSE market-data adapter.

Migrated from the user's previous ApexQuant `angel_feed.py` integration and
adapted to TRADEIQ's Decimal/provenance contracts. No order APIs are exposed.
"""
import datetime
import json
import re
import threading
import time
import urllib.request
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Callable, Dict, List, Optional

from ..domain.calendar.nse_calendar import IST, evaluate_freshness, to_ist
from .base import MarketDataProvider, NormalizedCandle, NormalizedInstrument, NormalizedQuote, ProvenanceEnvelope

MASTER_URL = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
INTERVALS = {
    "1m": "ONE_MINUTE", "3m": "THREE_MINUTE", "5m": "FIVE_MINUTE",
    "10m": "TEN_MINUTE", "15m": "FIFTEEN_MINUTE", "30m": "THIRTY_MINUTE",
    "1h": "ONE_HOUR", "1d": "ONE_DAY",
}
INTERVAL_MINUTES = {"1m": 1, "3m": 3, "5m": 5, "10m": 10, "15m": 15, "30m": 30, "1h": 60, "1d": 1440}
_OBJ_RE = re.compile(r"\{[^{}]*\}")
_NSE_RE = re.compile(r'"exch_seg"\s*:\s*"NSE"')
_EQ_RE = re.compile(r'"symbol"\s*:\s*"[^"]*-EQ"')


def _round_tick(value: Decimal, tick: Decimal) -> Decimal:
    if tick <= 0:
        tick = Decimal("0.05")
    units = (value / tick).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return (units * tick).quantize(Decimal("0.0001"))


def normalize_angel_ws_tick(msg: Any, token_to_symbol: Dict[str, str], now_utc: Optional[datetime.datetime] = None, token_to_tick: Optional[Dict[str, Decimal]] = None) -> Optional[NormalizedQuote]:
    """Angel WebSocket V2 prices are integer paise; convert to INR Decimal."""
    if not isinstance(msg, dict) or msg.get("last_traded_price") is None:
        return None
    try:
        token = str(msg.get("token", "")).strip('"\x00 ')
        symbol = token_to_symbol.get(token)
        if not symbol:
            return None
        now = now_utc or datetime.datetime.now(datetime.timezone.utc)
        exch_ms = msg.get("exchange_timestamp")
        exch = datetime.datetime.fromtimestamp(float(exch_ms) / 1000, tz=datetime.timezone.utc) if exch_ms else now
        price = lambda k, default: Decimal(str(msg.get(k, default))) / Decimal("100")
        tick = (token_to_tick or {}).get(token, Decimal("0.05"))
        ltp = _round_tick(price("last_traded_price", 0), tick)
        opn = _round_tick(price("open_price_of_the_day", msg["last_traded_price"]), tick)
        high = max(ltp, opn, _round_tick(price("high_price_of_the_day", msg["last_traded_price"]), tick))
        low = min(ltp, opn, _round_tick(price("low_price_of_the_day", msg["last_traded_price"]), tick))
        prev = _round_tick(price("closed_price", msg["last_traded_price"]), tick)
        volume = max(0, int(msg.get("volume_trade_for_the_day") or 0))
        if ltp <= 0:
            return None
        prov = ProvenanceEnvelope(
            source="ANGEL_ONE", exchange_ts_ist=to_ist(exch).isoformat(),
            ingested_ts_utc=now.isoformat(),
            freshness=evaluate_freshness(exch, now, base_class="LIVE", max_live_age_seconds=90),
            quality_status="VALID", is_simulated=False,
        )
        return NormalizedQuote(f"nse-{symbol.lower()}", symbol, "NSE", ltp, prev, opn, high, low, volume, prov)
    except (ValueError, TypeError, KeyError, OverflowError):
        return None


def parse_scrip_master_chunk(chunk_text: str) -> Dict[str, NormalizedInstrument]:
    out: Dict[str, NormalizedInstrument] = {}
    for match in _OBJ_RE.finditer(chunk_text):
        raw = match.group(0)
        if not (_NSE_RE.search(raw) and _EQ_RE.search(raw)):
            continue
        try:
            item = json.loads(raw)
            symbol = str(item.get("name") or "").strip().upper()
            token = str(item.get("token") or "").strip()
            if not symbol or not token:
                continue
            tick = Decimal(str(item.get("tick_size") or "5")) / Decimal("100")
            lot = max(1, int(item.get("lotsize") or 1))
            out[symbol] = NormalizedInstrument(
                instrument_id=f"nse-{symbol.lower()}", exchange="NSE", segment="CASH_EQUITY",
                symbol=symbol, name=str(item.get("symbol") or symbol), provider_token=token,
                tick_size=tick if tick > 0 else Decimal("0.05"), lot_size=lot,
                sector="NSE_EQ", status="ACTIVE", source="ANGEL_ONE",
            )
        except Exception:
            continue
    return out


class AngelOneProvider(MarketDataProvider):
    """Authenticated read-only SmartAPI adapter with historical candles and WS quotes."""
    def __init__(self, api_key: str = "", client_code: str = "", password: str = "", totp_secret: str = "") -> None:
        self.api_key, self.client_code = api_key.strip(), client_code.strip()
        self.password, self.totp_secret = password.strip(), totp_secret.replace(" ", "").strip()
        self.smart: Any = None
        self.jwt_token: Optional[str] = None
        self.feed_token: Optional[str] = None
        self.session_created_ist: Optional[datetime.datetime] = None
        self._instruments: Dict[str, NormalizedInstrument] = {}
        self._latest_quotes: Dict[str, NormalizedQuote] = {}
        self._ws: Any = None
        self._ws_thread: Optional[threading.Thread] = None
        self._ws_connected = False
        self._last_tick_epoch = 0.0
        self._last_error: Optional[str] = None
        self._last_request_ts = 0.0
        self._lock = threading.RLock()

    def is_configured(self) -> bool:
        return all((self.api_key, self.client_code, self.password, self.totp_secret))

    def is_session_expired(self, now_dt: Optional[datetime.datetime] = None) -> bool:
        if not self.jwt_token or not self.session_created_ist:
            return True
        current = to_ist(now_dt or datetime.datetime.now(datetime.timezone.utc))
        return current.date() > self.session_created_ist.date()

    def connection_state(self, now_dt: Optional[datetime.datetime] = None) -> str:
        if not self.is_configured(): return "NOT_CONFIGURED"
        if not self.jwt_token: return "DISCONNECTED"
        if self.is_session_expired(now_dt): return "RECONNECT_REQUIRED"
        if self._ws_connected and time.time() - self._last_tick_epoch < 90: return "CONNECTED"
        return "AUTHENTICATED_NO_RECENT_TICKS"

    def status_summary(self) -> Dict[str, Any]:
        return {"configured": self.is_configured(), "authenticated": bool(self.jwt_token and not self.is_session_expired()),
                "websocket_connected": self._ws_connected, "last_tick_epoch": self._last_tick_epoch or None,
                "instrument_count": len(self._instruments), "last_error": self._last_error,
                "connection_state": self.connection_state()}

    def login(self) -> None:
        if not self.is_configured():
            raise RuntimeError("Angel One credentials are incomplete")
        from SmartApi import SmartConnect
        import pyotp
        smart = SmartConnect(api_key=self.api_key)
        response = smart.generateSession(self.client_code, self.password, pyotp.TOTP(self.totp_secret).now())
        if not isinstance(response, dict) or not response.get("status") or not isinstance(response.get("data"), dict):
            message = response.get("message", "unknown login failure") if isinstance(response, dict) else "invalid response"
            self._last_error = str(message)[:180]
            raise RuntimeError(f"Angel One login failed: {self._last_error}")
        data = response["data"]
        self.smart = smart
        self.jwt_token = data.get("jwtToken")
        self.feed_token = data.get("feedToken") or smart.getfeedToken()
        self.session_created_ist = datetime.datetime.now(IST)
        self._last_error = None
        if not self.jwt_token:
            raise RuntimeError("Angel One login response did not include JWT token")

    def refresh_instruments(self, wanted_symbols: Optional[List[str]] = None) -> List[NormalizedInstrument]:
        req = urllib.request.Request(MASTER_URL, headers={"User-Agent": "TRADEIQ/1.0"})
        found: Dict[str, NormalizedInstrument] = {}
        buf = ""
        with urllib.request.urlopen(req, timeout=60) as response:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk: break
                buf += chunk.decode("utf-8", errors="ignore")
                cut = buf.rfind("}")
                if cut >= 0:
                    found.update(parse_scrip_master_chunk(buf[:cut + 1]))
                    buf = buf[cut + 1:]
        if not found:
            raise RuntimeError("Angel One instrument master returned no NSE equity instruments")
        if wanted_symbols:
            wanted = {s.upper().replace("-EQ", "") for s in wanted_symbols}
            found = {s: inst for s, inst in found.items() if s in wanted}
        with self._lock:
            self._instruments.update(found)
        return list(found.values())

    def get_instruments(self) -> List[NormalizedInstrument]:
        with self._lock: return list(self._instruments.values())

    def _throttle(self, min_interval: float = 0.40) -> None:
        elapsed = time.monotonic() - self._last_request_ts
        if elapsed < min_interval: time.sleep(min_interval - elapsed)
        self._last_request_ts = time.monotonic()

    def get_quote(self, symbol: str) -> Optional[NormalizedQuote]:
        sym = symbol.strip().upper().replace("-EQ", "")
        with self._lock: quote = self._latest_quotes.get(sym)
        if quote and quote.provenance.freshness not in ("STALE", "UNAVAILABLE"):
            return quote
        return None

    def get_candles(self, symbol: str, timeframe: str = "1d", limit: int = 30) -> List[NormalizedCandle]:
        if not self.smart or self.is_session_expired():
            return []
        sym = symbol.strip().upper().replace("-EQ", "")
        inst = self._instruments.get(sym)
        if not inst:
            try: self.refresh_instruments([sym])
            except Exception as exc:
                self._last_error = f"instrument lookup: {type(exc).__name__}"
                return []
            inst = self._instruments.get(sym)
        interval = INTERVALS.get(timeframe.lower())
        if not inst or not interval:
            return []
        minutes = INTERVAL_MINUTES[timeframe.lower()]
        now = datetime.datetime.now(IST)
        # Request enough calendar days to cover weekends/holidays and requested bars.
        days = max(5, int(limit * minutes / 375 * 2.2) + 4) if minutes < 1440 else max(45, limit * 2)
        start = now - datetime.timedelta(days=min(days, 365))
        params = {"exchange": "NSE", "symboltoken": inst.provider_token, "interval": interval,
                  "fromdate": start.strftime("%Y-%m-%d %H:%M"), "todate": now.strftime("%Y-%m-%d %H:%M")}
        self._throttle()
        try:
            response = self.smart.getCandleData(params)
            rows = response.get("data") if isinstance(response, dict) and response.get("status") else None
            if not isinstance(rows, list):
                self._last_error = str(response.get("message", "candle API returned no data"))[:180] if isinstance(response, dict) else "invalid candle response"
                return []
            out: List[NormalizedCandle] = []
            for row in rows[-limit:]:
                if not isinstance(row, (list, tuple)) or len(row) < 6: continue
                try:
                    dt = datetime.datetime.fromisoformat(str(row[0]).replace("Z", "+00:00"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=IST)
                    dt = to_ist(dt)
                    vals = [Decimal(str(v)) for v in row[1:5]]
                    vol = max(0, int(row[5]))
                    closed = dt + datetime.timedelta(minutes=minutes) <= now
                    freshness = "HISTORICAL" if closed else "DELAYED"
                    prov = ProvenanceEnvelope("ANGEL_ONE", dt.isoformat(), datetime.datetime.now(datetime.timezone.utc).isoformat(), freshness, "VALID", False)
                    out.append(NormalizedCandle(f"nse-{sym.lower()}", sym, timeframe.lower(), dt.isoformat(), vals[0], vals[1], vals[2], vals[3], vol, closed, "UNADJUSTED", prov))
                except Exception: continue
            self._last_error = None
            return out
        except Exception as exc:
            self._last_error = f"candle request: {type(exc).__name__}: {str(exc)[:120]}"
            return []

    def start_websocket(self, symbols: List[str], on_quote: Optional[Callable[[NormalizedQuote], None]] = None) -> None:
        if not self.smart or self.is_session_expired():
            raise RuntimeError("Angel One must be authenticated before starting WebSocket")
        from SmartApi.smartWebSocketV2 import SmartWebSocketV2
        tokens = [self._instruments[s.upper()].provider_token for s in symbols if s.upper() in self._instruments]
        if not tokens: raise RuntimeError("No valid NSE tokens available for WebSocket subscription")
        ws = SmartWebSocketV2(self.jwt_token, self.api_key, self.client_code, self.feed_token, max_retry_attempt=5)
        token_map = {inst.provider_token: sym for sym, inst in self._instruments.items()}
        tick_map = {inst.provider_token: inst.tick_size for inst in self._instruments.values()}
        def opened(*_):
            self._ws_connected = True
            self._last_error = None
            for i in range(0, len(tokens), 100):
                ws.subscribe("tradeiq_nse", 2, [{"exchangeType": 1, "tokens": tokens[i:i+100]}])
        def data(*args):
            msg = args[-1] if args else None
            quote = normalize_angel_ws_tick(msg, token_map, token_to_tick=tick_map)
            if quote:
                with self._lock: self._latest_quotes[quote.symbol] = quote
                self._last_tick_epoch = time.time()
                if on_quote:
                    try: on_quote(quote)
                    except Exception as exc: self._last_error = f"quote callback: {type(exc).__name__}"
        def error(*args): self._last_error = str(args[-1] if args else "websocket error")[:180]
        def closed(*_): self._ws_connected = False
        ws.on_open, ws.on_data, ws.on_error, ws.on_close = opened, data, error, closed
        self._ws = ws
        def run():
            try: ws.connect()
            except Exception as exc: self._last_error = f"websocket: {type(exc).__name__}: {str(exc)[:120]}"
            finally: self._ws_connected = False
        self._ws_thread = threading.Thread(target=run, name="tradeiq-angel-ws", daemon=True)
        self._ws_thread.start()

    def stop(self) -> None:
        try:
            if self._ws: self._ws.close_connection()
        except Exception: pass
        self._ws_connected = False
        self._ws = None
