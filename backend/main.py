import asyncio
import json
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

import feedparser
import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
COINGECKO_DEMO_KEY = os.getenv("COINGECKO_DEMO_KEY", "").strip()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "").strip()

BASE_DIR = Path(__file__).resolve().parent
FRONTEND_FILE = BASE_DIR.parent / "frontend" / "index.html"
LOCAL_TRADES_FILE = BASE_DIR / "trades_backup.json"

_CACHE: Dict[str, Dict[str, Any]] = {}
_LAST_ALERTED: Dict[str, float] = {}


def get_cache(key: str, ttl_sec: int = 90) -> Optional[Any]:
    entry = _CACHE.get(key)
    if entry and (time.time() - entry["ts"]) < ttl_sec:
        return entry["data"]
    return None


def set_cache(key: str, data: Any) -> None:
    _CACHE[key] = {"ts": time.time(), "data": data}


def calc_ema(prices: List[float], period: int) -> Optional[float]:
    if len(prices) < period:
        return None
    k = 2 / (period + 1)
    ema = sum(prices[:period]) / period
    for p in prices[period:]:
        ema = p * k + ema * (1 - k)
    return ema


def calc_rsi(prices: List[float], period: int = 14) -> Optional[float]:
    if len(prices) <= period:
        return None
    gains, losses = 0.0, 0.0
    for i in range(1, period + 1):
        diff = prices[i] - prices[i - 1]
        if diff >= 0:
            gains += diff
        else:
            losses -= diff
    avg_gain = gains / period
    avg_loss = losses / period
    for i in range(period + 1, len(prices)):
        diff = prices[i] - prices[i - 1]
        avg_gain = (avg_gain * (period - 1) + (diff if diff > 0 else 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + (-diff if diff < 0 else 0.0)) / period
    if avg_loss == 0:
        return 100.0
    return 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))


async def send_telegram_message(text: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                url,
                json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"},
                timeout=10.0,
            )
            return r.status_code == 200
    except Exception:
        return False


async def fetch_btc_dominance(client: httpx.AsyncClient) -> float:
    cached = get_cache("btc_dom", ttl_sec=300)
    if cached is not None:
        return cached
    dom = 58.0
    try:
        headers = {"x-cg-demo-api-key": COINGECKO_DEMO_KEY} if COINGECKO_DEMO_KEY else {}
        r = await client.get("https://api.coingecko.com/api/v3/global", headers=headers, timeout=8.0)
        if r.status_code == 200:
            dom = float(r.json().get("data", {}).get("market_cap_percentage", {}).get("btc", 58.0))
            set_cache("btc_dom", round(dom, 2))
    except Exception:
        pass
    return round(dom, 2)


async def fetch_coindcx_inr_tickers(client: httpx.AsyncClient) -> Dict[str, float]:
    cached = get_cache("coindcx_inr", ttl_sec=45)
    if cached is not None:
        return cached
    inr_map: Dict[str, float] = {}
    try:
        resp = await client.get("https://api.coindcx.com/exchange/ticker", timeout=8.0)
        if resp.status_code == 200:
            for item in resp.json():
                market = item.get("market", "")
                if market.endswith("INR"):
                    sym = market[:-3].lower()
                    try:
                        inr_map[sym] = float(item.get("last_price", 0))
                    except (TypeError, ValueError):
                        pass
        set_cache("coindcx_inr", inr_map)
    except Exception:
        pass
    return inr_map


async def build_scanner_data(limit: int = 25) -> Dict[str, Any]:
    cache_key = f"scanner_{limit}"
    cached = get_cache(cache_key, ttl_sec=90)
    if cached:
        return cached

    headers = {"x-cg-demo-api-key": COINGECKO_DEMO_KEY} if COINGECKO_DEMO_KEY else {}
    async with httpx.AsyncClient() as client:
        cg_url = (
            "https://api.coingecko.com/api/v3/coins/markets"
            f"?vs_currency=inr&order=market_cap_desc&per_page={limit}&page=1"
            "&sparkline=true&price_change_percentage=24h,7d,14d,30d"
        )
        cg_resp = await client.get(cg_url, headers=headers, timeout=12.0)
        if cg_resp.status_code != 200:
            raise HTTPException(status_code=502, detail=f"CoinGecko error: {cg_resp.status_code}")

        coins = cg_resp.json()
        dcx_inr, btc_dom = await asyncio.gather(
            fetch_coindcx_inr_tickers(client),
            fetch_btc_dominance(client),
        )

    alt_season_boost = 5.0 if btc_dom < 56.0 else 0.0
    results = []
    for c in coins:
        sym = (c.get("symbol") or "").lower()
        if sym in {"usdt", "usdc", "dai", "fdusd", "tusd"}:
            continue

        cg_price_inr = c.get("current_price") or 0.0
        dcx_price_inr = dcx_inr.get(sym)
        price_inr = dcx_price_inr if dcx_price_inr else cg_price_inr

        pct_24h = c.get("price_change_percentage_24h_in_currency") or 0.0
        pct_7d = c.get("price_change_percentage_7d_in_currency") or 0.0
        pct_14d = c.get("price_change_percentage_14d_in_currency") or 0.0
        pct_30d = c.get("price_change_percentage_30d_in_currency") or 0.0
        pct_10d = round(pct_7d + (pct_14d - pct_7d) * (3.0 / 7.0), 2)

        mcap = c.get("market_cap") or 1.0
        vol = c.get("total_volume") or 0.0
        vol_mcap_ratio = (vol / mcap) * 100.0

        score = 40.0 + (alt_season_boost if sym != "btc" else 0.0)
        if vol_mcap_ratio > 8.0:
            score += 20.0
        elif vol_mcap_ratio > 4.0:
            score += 10.0

        if pct_7d > 0 and pct_30d > 0:
            score += 20.0
        elif pct_7d < -8.0 and pct_24h > 0:
            score += 25.0

        if pct_10d > pct_30d / 3.0:
            score += 10.0

        score = round(min(100.0, max(0.0, score)), 1)
        signal = (
            "Oversold Bounce" if (pct_7d < -8.0 and pct_24h > 0)
            else "Breakout Momentum" if (score >= 70 and pct_7d > 3.0)
            else "Accumulation" if score >= 60
            else "Watchlist"
        )

        results.append({
            "id": c.get("id"),
            "symbol": sym.upper(),
            "name": c.get("name"),
            "price_inr": round(price_inr, 2),
            "price_source": "CoinDCX INR" if dcx_price_inr else "CoinGecko INR",
            "change_24h_pct": round(pct_24h, 2),
            "change_7d_pct": round(pct_7d, 2),
            "change_10d_pct": pct_10d,
            "change_30d_pct": round(pct_30d, 2),
            "vol_mcap_ratio_pct": round(vol_mcap_ratio, 2),
            "swing_score": score,
            "signal": signal,
        })

    results.sort(key=lambda x: x["swing_score"], reverse=True)
    payload = {"btc_dominance": btc_dom, "count": len(results), "coins": results}
    set_cache(cache_key, payload)
    return payload


async def telegram_alert_loop():
    """Phase 1: Background scanner every 2 hours for 75+ Swing Score setups."""
    while True:
        await asyncio.sleep(7200)
        if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
            continue
        try:
            data = await build_scanner_data(limit=25)
            top = [c for c in data["coins"] if c["swing_score"] >= 75][:3]
            now = time.time()
            lines = []
            for c in top:
                if now - _LAST_ALERTED.get(c["symbol"], 0) > 21600:
                    _LAST_ALERTED[c["symbol"]] = now
                    lines.append(
                        f"• <b>{c['symbol']}</b>: ₹{c['price_inr']:,.2f} | Score <b>{c['swing_score']}</b> ({c['signal']})\n"
                        f"  7D: {c['change_7d_pct']}% | 30D: {c['change_30d_pct']}%"
                    )
            if lines:
                msg = (
                    f"🚀 <b>ApexQuant INR Spot Swing Alert</b>\n"
                    f"BTC Dominance: {data['btc_dominance']}%\n\n" + "\n".join(lines)
                )
                await send_telegram_message(msg)
        except Exception:
            pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(telegram_alert_loop())
    yield
    task.cancel()


app = FastAPI(
    title="ApexQuant INR Spot Swing v2",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/", include_in_schema=False)
async def serve_dashboard():
    if FRONTEND_FILE.exists():
        return FileResponse(FRONTEND_FILE)
    return {"status": "ApexQuant v2 API running. Visit /docs"}


@app.get("/health")
async def health_check():
    """Phase 1: UptimeRobot ping endpoint to keep Render awake."""
    return {"status": "ok", "ts": int(time.time())}


@app.get("/api/scanner")
async def scan_rising_coins(limit: int = Query(25, ge=5, le=100)):
    return await build_scanner_data(limit=limit)


@app.post("/api/alerts/test")
async def test_telegram_alert():
    """Sends an instant test alert to Telegram with the top scanner coin."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return {"sent": False, "detail": "Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in Render Environment Variables."}
    data = await build_scanner_data(limit=15)
    top = data["coins"][0] if data["coins"] else None
    if not top:
        return {"sent": False, "detail": "No coins returned"}
    msg = (
        f"✅ <b>ApexQuant Test Alert</b>\n"
        f"Top Pick: <b>{top['name']} ({top['symbol']})</b>\n"
        f"Price: ₹{top['price_inr']:,.2f} | Score: <b>{top['swing_score']}</b> ({top['signal']})\n"
        f"BTC Dominance: {data['btc_dominance']}%"
    )
    ok = await send_telegram_message(msg)
    return {"sent": ok, "coin": top["symbol"]}


@app.get("/api/news-macro")
async def get_news_and_macro():
    cached = get_cache("news_macro", ttl_sec=300)
    if cached:
        return cached

    fear_greed = {}
    headlines: List[str] = []
    btc_dom = 58.0

    async with httpx.AsyncClient() as client:
        btc_dom = await fetch_btc_dominance(client)
        try:
            fg_resp = await client.get("https://api.alternative.me/fng/?limit=1", timeout=8.0)
            if fg_resp.status_code == 200:
                fg_data = fg_resp.json().get("data", [{}])[0]
                fear_greed = {
                    "value": int(fg_data.get("value", 50)),
                    "classification": fg_data.get("value_classification", "Neutral"),
                }
        except Exception:
            fear_greed = {"value": 50, "classification": "Unavailable"}

        for rss_url in [
            "https://www.coindesk.com/arc/outboundfeeds/rss/",
            "https://cointelegraph.com/rss",
        ]:
            try:
                r = await client.get(rss_url, timeout=8.0)
                if r.status_code == 200:
                    parsed = feedparser.parse(r.text)
                    for entry in parsed.entries[:6]:
                        headlines.append(entry.title)
            except Exception:
                pass

    ai_summary = None
    if GEMINI_API_KEY and headlines:
        prompt = (
            "You are a pro crypto spot swing analyst. Summarize these latest headlines focusing on "
            "US Fed/macro policy, ETF flows, and top coin catalysts. Return valid JSON with keys: "
            "macro_bias (Bullish/Bearish/Neutral), summary (2-3 sentences), top_catalysts (list of up to 3 strings).\n\n"
            "Headlines:\n" + "\n".join(f"- {h}" for h in headlines[:10])
        )
        try:
            async with httpx.AsyncClient() as client:
                g_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
                g_resp = await client.post(
                    g_url,
                    json={
                        "contents": [{"parts": [{"text": prompt}]}],
                        "generationConfig": {"responseMimeType": "application/json"},
                    },
                    timeout=15.0,
                )
                if g_resp.status_code == 200:
                    raw_text = g_resp.json()["candidates"][0]["content"]["parts"][0]["text"]
                    ai_summary = json.loads(raw_text)
        except Exception as exc:
            ai_summary = {"error": f"Gemini analysis failed: {exc}"}
    elif not GEMINI_API_KEY:
        ai_summary = {"note": "Set GEMINI_API_KEY in Environment Variables for live AI summary."}

    payload = {
        "btc_dominance": btc_dom,
        "fear_and_greed": fear_greed,
        "headlines": headlines[:10],
        "gemini_analysis": ai_summary,
    }
    set_cache("news_macro", payload)
    return payload


class PaperTradeIn(BaseModel):
    symbol: str
    budget_inr: float
    buy_price_inr: float
    target_inr: float
    stop_loss_inr: float


def _load_local_trades() -> List[Dict[str, Any]]:
    if LOCAL_TRADES_FILE.exists():
        try:
            return json.loads(LOCAL_TRADES_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def _save_local_trades(trades: List[Dict[str, Any]]) -> None:
    LOCAL_TRADES_FILE.write_text(json.dumps(trades, indent=2), encoding="utf-8")


@app.get("/api/trades")
async def list_trades():
    """Phase 2: Persistent Paper/Spot Trade Log (Supabase REST or local JSON fallback)."""
    if SUPABASE_URL and SUPABASE_KEY:
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(
                    f"{SUPABASE_URL}/rest/v1/paper_trades?select=*&order=created_at.desc",
                    headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
                    timeout=8.0,
                )
                if r.status_code == 200:
                    return {"storage": "Supabase", "trades": r.json()}
        except Exception:
            pass
    return {"storage": "Local JSON", "trades": _load_local_trades()}


@app.post("/api/trades")
async def create_trade(trade: PaperTradeIn):
    row = {
        "id": str(uuid.uuid4())[:8],
        "symbol": trade.symbol.upper(),
        "budget_inr": round(trade.budget_inr, 2),
        "buy_price_inr": round(trade.buy_price_inr, 2),
        "target_inr": round(trade.target_inr, 2),
        "stop_loss_inr": round(trade.stop_loss_inr, 2),
        "status": "HOLDING",
        "created_at": int(time.time()),
    }
    if SUPABASE_URL and SUPABASE_KEY:
        try:
            async with httpx.AsyncClient() as client:
                r = await client.post(
                    f"{SUPABASE_URL}/rest/v1/paper_trades",
                    headers={
                        "apikey": SUPABASE_KEY,
                        "Authorization": f"Bearer {SUPABASE_KEY}",
                        "Content-Type": "application/json",
                        "Prefer": "return=representation",
                    },
                    json=row,
                    timeout=8.0,
                )
                if r.status_code in (200, 201):
                    return {"storage": "Supabase", "trade": r.json()[0]}
        except Exception:
            pass

    trades = _load_local_trades()
    trades.insert(0, row)
    _save_local_trades(trades[:50])
    return {"storage": "Local JSON", "trade": row}
