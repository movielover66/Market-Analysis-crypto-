import os
import time
import json
from typing import Any, Dict, List, Optional

import feedparser
import httpx
from dotenv import load_dotenv
from pathlib import Path
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

app = FastAPI(
    title="ApexQuant INR Spot Swing & News API",
    version="1.0.0",
    description="7D/10D/30D Rising Coin Scanner (CoinGecko + CoinDCX INR) & Gemini News Analyzer",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
COINGECKO_DEMO_KEY = os.getenv("COINGECKO_DEMO_KEY", "").strip()

# Simple in-memory TTL cache to respect free API rate limits
_CACHE: Dict[str, Dict[str, Any]] = {}


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
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


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


@app.get("/api/scanner")
async def scan_rising_coins(limit: int = Query(30, ge=5, le=100)):
    """
    Scans top coins in INR using CoinGecko + CoinDCX public APIs
    and ranks them by 7D, 10D (approx), 30D momentum & volume breakout score.
    """
    cache_key = f"scanner_{limit}"
    cached = get_cache(cache_key, ttl_sec=90)
    if cached:
        return cached

    headers = {}
    if COINGECKO_DEMO_KEY:
        headers["x-cg-demo-api-key"] = COINGECKO_DEMO_KEY

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
        dcx_inr = await fetch_coindcx_inr_tickers(client)

    results = []
    for c in coins:
        sym = (c.get("symbol") or "").lower()
        cg_price_inr = c.get("current_price") or 0.0
        dcx_price_inr = dcx_inr.get(sym)
        price_inr = dcx_price_inr if dcx_price_inr else cg_price_inr

        pct_24h = c.get("price_change_percentage_24h_in_currency") or 0.0
        pct_7d = c.get("price_change_percentage_7d_in_currency") or 0.0
        pct_14d = c.get("price_change_percentage_14d_in_currency") or 0.0
        pct_30d = c.get("price_change_percentage_30d_in_currency") or 0.0

        # Interpolate 10D return from 7D and 14D
        pct_10d = round(pct_7d + (pct_14d - pct_7d) * (3.0 / 7.0), 2)

        mcap = c.get("market_cap") or 1.0
        vol = c.get("total_volume") or 0.0
        vol_mcap_ratio = (vol / mcap) * 100.0

        # Spot Swing Breakout Score (0-100): favors rising volume + recovering/positive 7D-30D
        score = 40.0
        if vol_mcap_ratio > 8.0:
            score += 20.0
        elif vol_mcap_ratio > 4.0:
            score += 10.0

        if pct_7d > 0 and pct_30d > 0:
            score += 20.0
        elif pct_7d < -8.0 and pct_24h > 0:
            score += 25.0  # Oversold dip bounce candidate

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
    payload = {"count": len(results), "coins": results}
    set_cache(cache_key, payload)
    return payload


@app.get("/api/coin/{coin_id}")
async def analyze_coin_spot_swing(coin_id: str, budget_inr: float = Query(1000.0, gt=0)):
    """
    Fetches 30-day INR price history from CoinGecko and computes exact
    7D, 10D, 30D changes, RSI(14), EMA(20), and Spot Buy/SL/Target levels in INR.
    """
    headers = {}
    if COINGECKO_DEMO_KEY:
        headers["x-cg-demo-api-key"] = COINGECKO_DEMO_KEY

    async with httpx.AsyncClient() as client:
        url = f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart?vs_currency=inr&days=30&interval=daily"
        resp = await client.get(url, headers=headers, timeout=12.0)
        if resp.status_code != 200:
            raise HTTPException(status_code=404, detail=f"Coin '{coin_id}' not found on CoinGecko")
        data = resp.json()

    prices = [p[1] for p in data.get("prices", [])]
    if len(prices) < 15:
        raise HTTPException(status_code=422, detail="Not enough historical points")

    current = prices[-1]
    p7 = prices[-8] if len(prices) >= 8 else prices[0]
    p10 = prices[-11] if len(prices) >= 11 else prices[0]
    p30 = prices[0]

    rsi14 = calc_rsi(prices, 14)
    ema20 = calc_ema(prices, 20)

    # Swing levels based on recent 10-day range
    recent_low = min(prices[-10:])
    recent_high = max(prices[-10:])
    sl_inr = round(recent_low * 0.97, 2)
    tp1_inr = round(current * 1.06, 2)
    tp2_inr = round(max(recent_high, current * 1.12), 2)
    qty = budget_inr / current

    return {
        "coin_id": coin_id,
        "current_price_inr": round(current, 2),
        "change_7d_pct": round(((current - p7) / p7) * 100, 2),
        "change_10d_pct": round(((current - p10) / p10) * 100, 2),
        "change_30d_pct": round(((current - p30) / p30) * 100, 2),
        "rsi_14": round(rsi14, 2) if rsi14 is not None else None,
        "ema_20_inr": round(ema20, 2) if ema20 is not None else None,
        "spot_swing_plan": {
            "buy_zone_inr": round(current, 2),
            "stop_loss_inr": sl_inr,
            "target_1_inr": tp1_inr,
            "target_2_inr": tp2_inr,
            "budget_inr": budget_inr,
            "estimated_qty": round(qty, 6),
            "est_profit_tp1_inr": round(qty * (tp1_inr - current), 2),
        },
    }


@app.get("/api/news-macro")
async def get_news_and_macro():
    """
    Fetches Crypto Fear & Greed Index + RSS headlines and uses Gemini API
    to summarize US Fed, ETF, and market catalysts.
    """
    cached = get_cache("news_macro", ttl_sec=300)
    if cached:
        return cached

    fear_greed = {}
    headlines: List[str] = []

    async with httpx.AsyncClient() as client:
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
        ai_summary = {"note": "Set GEMINI_API_KEY in .env to enable live AI macro & news scoring."}

    payload = {
        "fear_and_greed": fear_greed,
        "headlines": headlines[:10],
        "gemini_analysis": ai_summary,
    }
    set_cache("news_macro", payload)
    return payload


FRONTEND_FILE = Path(__file__).resolve().parent.parent / "frontend" / "index.html"


@app.get("/", include_in_schema=False)
async def serve_dashboard():
    if FRONTEND_FILE.exists():
        return FileResponse(FRONTEND_FILE)
    return {"status": "ApexQuant API running. Visit /docs for endpoints."}
