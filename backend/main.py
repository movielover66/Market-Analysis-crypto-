import asyncio
import csv
import datetime
import io
import json
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import feedparser
import httpx
import yfinance as yf
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
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
_LAST_BRIEFING_DATE: str = ""
_LAST_NSE_SCAN_DATE: str = ""

NSE_UNIVERSE = [
    ("RELIANCE.NS", "Reliance Industries"),
    ("TCS.NS", "Tata Consultancy"),
    ("HDFCBANK.NS", "HDFC Bank"),
    ("ICICIBANK.NS", "ICICI Bank"),
    ("SBIN.NS", "State Bank of India"),
    ("INFY.NS", "Infosys"),
    ("TATAMOTORS.NS", "Tata Motors"),
    ("BHARTIARTL.NS", "Bharti Airtel"),
    ("ITC.NS", "ITC Ltd"),
    ("LT.NS", "Larsen & Toubro"),
    ("AXISBANK.NS", "Axis Bank"),
    ("BAJFINANCE.NS", "Bajaj Finance"),
    ("SUNPHARMA.NS", "Sun Pharma"),
    ("MARUTI.NS", "Maruti Suzuki"),
    ("TITAN.NS", "Titan Company"),
    ("TRENT.NS", "Trent Ltd"),
    ("HAL.NS", "Hindustan Aeronautics"),
    ("BEL.NS", "Bharat Electronics"),
]


def format_duration(seconds: int) -> str:
    seconds = max(60, int(seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    mins = rem // 60
    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0:
        parts.append(f"{hours}h")
    if mins > 0 or not parts:
        parts.append(f"{mins}m")
    return " ".join(parts)


def get_cache(key: str, ttl_sec: int = 300) -> Optional[Any]:
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


def calc_atr(candles: List[Dict[str, float]], period: int = 14) -> float:
    if len(candles) < period + 1:
        return candles[-1]["c"] * 0.02 if candles else 1.0
    trs = []
    for i in range(1, len(candles)):
        cur, prev = candles[i], candles[i - 1]
        trs.append(max(cur["h"] - cur["l"], abs(cur["h"] - prev["c"]), abs(cur["l"] - prev["c"])))
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return atr


def calc_adx_and_obv(candles: List[Dict[str, float]], period: int = 14) -> Tuple[float, bool, float]:
    if len(candles) < period + 5:
        return 18.0, False, 1.0
    trs, plus_dms, minus_dms = [], [], []
    obv = [0.0]
    for i in range(1, len(candles)):
        cur, prev = candles[i], candles[i - 1]
        tr = max(cur["h"] - cur["l"], abs(cur["h"] - prev["c"]), abs(cur["l"] - prev["c"]))
        up = cur["h"] - prev["h"]
        down = prev["l"] - cur["l"]
        plus_dms.append(up if (up > down and up > 0) else 0.0)
        minus_dms.append(down if (down > up and down > 0) else 0.0)
        trs.append(tr)
        if cur["c"] > prev["c"]:
            obv.append(obv[-1] + cur["v"])
        elif cur["c"] < prev["c"]:
            obv.append(obv[-1] - cur["v"])
        else:
            obv.append(obv[-1])

    tr_s = sum(trs[:period])
    p_s = sum(plus_dms[:period])
    m_s = sum(minus_dms[:period])
    dx_list = []
    for i in range(period, len(trs)):
        tr_s = tr_s - (tr_s / period) + trs[i]
        p_s = p_s - (p_s / period) + plus_dms[i]
        m_s = m_s - (m_s / period) + minus_dms[i]
        if tr_s == 0:
            continue
        p_di = 100.0 * (p_s / tr_s)
        m_di = 100.0 * (m_s / tr_s)
        denom = p_di + m_di
        dx = 100.0 * abs(p_di - m_di) / denom if denom > 0 else 0.0
        dx_list.append(dx)

    adx = sum(dx_list[-period:]) / max(1, min(period, len(dx_list)))
    obv_rising = obv[-1] > obv[-6] if len(obv) >= 6 else False
    avg_vol = sum(c["v"] for c in candles[-21:-1]) / 20.0
    rvol = (candles[-1]["v"] / avg_vol) if avg_vol > 0 else 1.0
    return round(adx, 1), obv_rising, round(rvol, 2)


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


# ==================== CRYPTO ENGINE ====================
async def fetch_4h_technicals(client: httpx.AsyncClient, symbol: str) -> Optional[Dict[str, Any]]:
    pair = f"{symbol.upper()}USDT"
    try:
        r = await client.get(
            f"https://api.binance.com/api/v3/klines?symbol={pair}&interval=4h&limit=60",
            timeout=6.0,
        )
        if r.status_code != 200:
            return None
        candles = [
            {"o": float(k[1]), "h": float(k[2]), "l": float(k[3]), "c": float(k[4]), "v": float(k[5])}
            for k in r.json()
        ]
        closes = [c["c"] for c in candles]
        ema20 = calc_ema(closes, 20)
        rsi14 = calc_rsi(closes, 14)
        adx14, obv_rising, rvol = calc_adx_and_obv(candles, 14)
        return {
            "above_ema20": (closes[-1] > ema20) if ema20 else False,
            "rsi_4h": round(rsi14, 1) if rsi14 else 50.0,
            "adx_4h": adx14,
            "obv_rising": obv_rising,
            "rvol": rvol,
        }
    except Exception:
        return None


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
    cache_key = f"scanner_v5_{limit}"
    cached = get_cache(cache_key, ttl_sec=300)
    if cached:
        return cached

    headers = {"x-cg-demo-api-key": COINGECKO_DEMO_KEY} if COINGECKO_DEMO_KEY else {}
    coins = None
    async with httpx.AsyncClient() as client:
        dcx_inr, btc_dom = await asyncio.gather(
            fetch_coindcx_inr_tickers(client),
            fetch_btc_dominance(client),
        )
        try:
            cg_url = (
                "https://api.coingecko.com/api/v3/coins/markets"
                f"?vs_currency=inr&order=market_cap_desc&per_page={limit}&page=1"
                "&sparkline=true&price_change_percentage=24h,7d,14d,30d"
            )
            cg_resp = await client.get(cg_url, headers=headers, timeout=10.0)
            if cg_resp.status_code == 200:
                coins = cg_resp.json()
        except Exception:
            coins = None

        if coins is None:
            stale = _CACHE.get(cache_key)
            if stale:
                return stale["data"]
            top_syms = ["BTC", "ETH", "SOL", "XRP", "BNB", "ADA", "DOGE", "AVAX", "DOT", "LINK", "NEAR", "SUI"]
            try:
                b_resp = await client.get("https://api.binance.com/api/v3/ticker/24hr", timeout=10.0)
                b_map = {x["symbol"]: x for x in b_resp.json()} if b_resp.status_code == 200 else {}
            except Exception:
                b_map = {}
            coins = []
            for sym in top_syms:
                t = b_map.get(sym + "USDT", {})
                usd_p = float(t.get("lastPrice", 0) or 0)
                inr_p = dcx_inr.get(sym.lower()) or (usd_p * 94.22)
                if inr_p <= 0:
                    continue
                pct_24h = float(t.get("priceChangePercent", 0) or 0)
                coins.append({
                    "id": sym.lower(),
                    "symbol": sym.lower(),
                    "name": sym,
                    "current_price": inr_p,
                    "price_change_percentage_24h_in_currency": pct_24h,
                    "price_change_percentage_7d_in_currency": pct_24h * 1.2,
                    "price_change_percentage_14d_in_currency": pct_24h * 1.4,
                    "price_change_percentage_30d_in_currency": pct_24h * 1.8,
                })

        btc_7d = 0.0
        for c in coins:
            if (c.get("symbol") or "").lower() == "btc":
                btc_7d = c.get("price_change_percentage_7d_in_currency") or 0.0
                break

        valid_coins = [
            c for c in coins
            if (c.get("symbol") or "").lower() not in {"usdt", "usdc", "dai", "fdusd", "tusd"}
        ][:15]

        tech_list = await asyncio.gather(
            *[fetch_4h_technicals(client, c.get("symbol", "")) for c in valid_coins]
        )

    alt_season_boost = 5.0 if btc_dom < 56.0 else 0.0
    results = []
    for c, tech in zip(valid_coins, tech_list):
        if tech is None:
            continue  # Tradeable Filter: skip non-Binance/illiquid tokens like FIGR_HELOC
        sym = (c.get("symbol") or "").lower()
        cg_price_inr = c.get("current_price") or 0.0
        dcx_price_inr = dcx_inr.get(sym)
        price_inr = dcx_price_inr if dcx_price_inr else cg_price_inr

        pct_24h = c.get("price_change_percentage_24h_in_currency") or 0.0
        pct_7d = c.get("price_change_percentage_7d_in_currency") or 0.0
        pct_14d = c.get("price_change_percentage_14d_in_currency") or 0.0
        pct_30d = c.get("price_change_percentage_30d_in_currency") or 0.0
        pct_10d = round(pct_7d + (pct_14d - pct_7d) * (3.0 / 7.0), 2)

        rs_vs_btc = round(pct_7d - btc_7d, 2)
        above_ema20 = tech["above_ema20"]
        adx_4h = tech["adx_4h"]
        obv_rising = tech["obv_rising"]
        rvol = tech["rvol"]

        score = 35.0 + (alt_season_boost if sym != "btc" else 0.0)
        if above_ema20:
            score += 20.0
        if adx_4h >= 20.0:
            score += 15.0
        if obv_rising or rvol >= 1.25:
            score += 15.0
        if rs_vs_btc > 1.5:
            score += 10.0
        if pct_7d > 0 and pct_30d > 0:
            score += 10.0

        falling_knife = (not above_ema20) and (pct_30d < -15.0 or pct_7d < -7.0)
        if falling_knife:
            score = min(score, 54.0)

        score = round(min(100.0, max(0.0, score)), 1)
        signal = (
            "Falling Knife (Wait)" if falling_knife
            else "Confirmed Breakout" if (score >= 75 and above_ema20 and adx_4h >= 20)
            else "Trend Reversal" if (score >= 65 and above_ema20)
            else "Accumulation" if score >= 58
            else "Watchlist"
        )

        if adx_4h >= 28 and rvol >= 1.3:
            hold_label = "4-12 Hours (Fast)"
            tp_mult, sl_mult = 1.045, 0.975
        elif pct_30d > 0 and above_ema20:
            hold_label = "3-5 Days (Trend)"
            tp_mult, sl_mult = 1.09, 0.96
        else:
            hold_label = "1-2 Days (Swing)"
            tp_mult, sl_mult = 1.06, 0.97

        results.append({
            "id": c.get("id"),
            "symbol": sym.upper(),
            "name": c.get("name"),
            "price_inr": round(price_inr, 2),
            "target_inr": round(price_inr * tp_mult, 2),
            "stop_loss_inr": round(price_inr * sl_mult, 2),
            "hold_label": hold_label,
            "price_source": "CoinDCX INR" if dcx_price_inr else "Binance/Gecko INR",
            "change_24h_pct": round(pct_24h, 2),
            "change_7d_pct": round(pct_7d, 2),
            "change_10d_pct": pct_10d,
            "change_30d_pct": round(pct_30d, 2),
            "adx_4h": adx_4h,
            "above_ema20": above_ema20,
            "rs_vs_btc": rs_vs_btc,
            "swing_score": score,
            "signal": signal,
        })

    results.sort(key=lambda x: x["swing_score"], reverse=True)
    payload = {"btc_dominance": btc_dom, "count": len(results), "coins": results}
    set_cache(cache_key, payload)
    return payload


# ==================== INDIAN STOCKS (NSE) ENGINE ====================
def _sync_build_nse_scanner() -> Dict[str, Any]:
    tickers = ["^NSEI", "^INDIAVIX"] + [s[0] for s in NSE_UNIVERSE]
    df = yf.download(tickers, period="3mo", interval="1d", group_by="ticker", progress=False, threads=True)

    def extract_candles(sym: str) -> List[Dict[str, float]]:
        try:
            sub = df[sym].dropna()
            out = []
            for idx, row in sub.iterrows():
                out.append({
                    "t": int(idx.timestamp()),
                    "o": float(row["Open"]),
                    "h": float(row["High"]),
                    "l": float(row["Low"]),
                    "c": float(row["Close"]),
                    "v": float(row["Volume"] or 0),
                })
            return out
        except Exception:
            return []

    nifty_c = extract_candles("^NSEI")
    vix_c = extract_candles("^INDIAVIX")

    nifty_price = round(nifty_c[-1]["c"], 2) if nifty_c else 0.0
    nifty_ema50 = calc_ema([x["c"] for x in nifty_c], 50) if len(nifty_c) >= 50 else None
    nifty_bullish = (nifty_price > nifty_ema50) if nifty_ema50 else True
    nifty_7d = ((nifty_c[-1]["c"] - nifty_c[-6]["c"]) / nifty_c[-6]["c"] * 100) if len(nifty_c) >= 6 else 0.0
    india_vix = round(vix_c[-1]["c"], 2) if vix_c else 14.5
    regime_ok = nifty_bullish and (india_vix < 22.0)

    stocks = []
    for sym, name in NSE_UNIVERSE:
        c = extract_candles(sym)
        if len(c) < 35:
            continue
        closes = [x["c"] for x in c]
        price = closes[-1]
        ema20 = calc_ema(closes, 20) or price
        ema50 = calc_ema(closes, 50) or ema20
        rsi14 = calc_rsi(closes, 14) or 50.0
        atr14 = calc_atr(c, 14)
        adx14, obv_rising, rvol = calc_adx_and_obv(c, 14)

        pct_24h = ((closes[-1] - closes[-2]) / closes[-2]) * 100
        pct_7d = ((closes[-1] - closes[-6]) / closes[-6]) * 100 if len(closes) >= 6 else 0.0
        pct_10d = ((closes[-1] - closes[-9]) / closes[-9]) * 100 if len(closes) >= 9 else pct_7d
        pct_30d = ((closes[-1] - closes[-22]) / closes[-22]) * 100 if len(closes) >= 22 else pct_7d
        rs_vs_nifty = round(pct_7d - nifty_7d, 2)

        above_ema20 = price > ema20
        uptrend_stack = price > ema20 and ema20 > ema50

        score = 30.0
        if uptrend_stack:
            score += 20.0
        elif above_ema20:
            score += 10.0
        if adx14 >= 20.0:
            score += 15.0
        if obv_rising and rvol >= 1.2:
            score += 15.0
        elif obv_rising or rvol >= 1.2:
            score += 8.0
        if rs_vs_nifty > 1.5:
            score += 10.0
        if 48.0 <= rsi14 <= 68.0:
            score += 10.0

        falling_knife = (not above_ema20) or (pct_30d < -12.0 and not above_ema20)
        if falling_knife:
            score = min(score, 52.0)
        if not regime_ok:
            score = min(score, 64.0)

        score = round(min(100.0, max(0.0, score)), 1)
        signal = (
            "Falling Knife (Wait)" if falling_knife
            else "Confirmed Breakout" if (score >= 75 and uptrend_stack)
            else "Trend Swing" if score >= 65
            else "Accumulation" if score >= 55
            else "Watchlist"
        )

        sl = round(max(price * 0.85, price - atr14 * 1.5), 2)
        tp1 = round(price + atr14 * 2.25, 2)
        tp2 = round(price + atr14 * 4.0, 2)
        hold_label = "2-4 Days (Momentum)" if (adx14 >= 25 and rvol >= 1.3) else ("5-10 Days (Positional)" if uptrend_stack else "3-5 Days (Swing)")

        stocks.append({
            "symbol": sym,
            "short_symbol": sym.replace(".NS", ""),
            "name": name,
            "price_inr": round(price, 2),
            "target_inr": tp1,
            "target2_inr": tp2,
            "stop_loss_inr": sl,
            "hold_label": hold_label,
            "change_24h_pct": round(pct_24h, 2),
            "change_7d_pct": round(pct_7d, 2),
            "change_10d_pct": round(pct_10d, 2),
            "change_30d_pct": round(pct_30d, 2),
            "rsi_14": round(rsi14, 1),
            "adx_14": adx14,
            "rvol": rvol,
            "rs_vs_nifty": rs_vs_nifty,
            "swing_score": score,
            "signal": signal,
        })

    stocks.sort(key=lambda x: x["swing_score"], reverse=True)
    return {
        "nifty_50": nifty_price,
        "nifty_bullish": nifty_bullish,
        "india_vix": india_vix,
        "regime": "Bullish (Safe)" if regime_ok else "Caution (Nifty/VIX)",
        "count": len(stocks),
        "stocks": stocks,
    }


async def build_nse_scanner() -> Dict[str, Any]:
    cached = get_cache("nse_scanner_v5", ttl_sec=300)
    if cached:
        return cached
    data = await asyncio.to_thread(_sync_build_nse_scanner)
    set_cache("nse_scanner_v5", data)
    return data


# ==================== TRADE PERSISTENCE & MONITORS ====================
def _load_local_trades() -> List[Dict[str, Any]]:
    if LOCAL_TRADES_FILE.exists():
        try:
            return json.loads(LOCAL_TRADES_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def _save_local_trades(trades: List[Dict[str, Any]]) -> None:
    LOCAL_TRADES_FILE.write_text(json.dumps(trades, indent=2), encoding="utf-8")


async def fetch_all_trades() -> Tuple[str, List[Dict[str, Any]]]:
    if SUPABASE_URL and SUPABASE_KEY:
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(
                    f"{SUPABASE_URL}/rest/v1/paper_trades?select=*&order=created_at.desc",
                    headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
                    timeout=8.0,
                )
                if r.status_code == 200:
                    return "Supabase", r.json()
        except Exception:
            pass
    return "Local JSON", _load_local_trades()


async def insert_paper_trade(row: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
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
                    return "Supabase", r.json()[0]
                base_row = {k: row[k] for k in ["id", "symbol", "budget_inr", "buy_price_inr", "target_inr", "stop_loss_inr", "status", "created_at"]}
                r2 = await client.post(
                    f"{SUPABASE_URL}/rest/v1/paper_trades",
                    headers={
                        "apikey": SUPABASE_KEY,
                        "Authorization": f"Bearer {SUPABASE_KEY}",
                        "Content-Type": "application/json",
                        "Prefer": "return=representation",
                    },
                    json=base_row,
                    timeout=8.0,
                )
                if r2.status_code in (200, 201):
                    return "Supabase", r2.json()[0]
        except Exception:
            pass
    trades = _load_local_trades()
    trades.insert(0, row)
    _save_local_trades(trades[:100])
    return "Local JSON", row


async def update_trade_status(trade_id: str, new_status: str, exit_price: float = 0.0, duration_str: str = "") -> None:
    now_ts = int(time.time())
    if SUPABASE_URL and SUPABASE_KEY:
        try:
            async with httpx.AsyncClient() as client:
                r = await client.patch(
                    f"{SUPABASE_URL}/rest/v1/paper_trades?id=eq.{trade_id}",
                    headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", "Content-Type": "application/json"},
                    json={"status": new_status, "exit_price_inr": round(exit_price, 2), "closed_at": now_ts, "duration_str": duration_str},
                    timeout=8.0,
                )
                if r.status_code in (200, 204):
                    return
                await client.patch(
                    f"{SUPABASE_URL}/rest/v1/paper_trades?id=eq.{trade_id}",
                    headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", "Content-Type": "application/json"},
                    json={"status": new_status},
                    timeout=8.0,
                )
                return
        except Exception:
            pass
    trades = _load_local_trades()
    for t in trades:
        if t.get("id") == trade_id:
            t["status"] = new_status
            t["exit_price_inr"] = round(exit_price, 2)
            t["closed_at"] = now_ts
            t["duration_str"] = duration_str
    _save_local_trades(trades)


async def check_open_trades_tp_sl():
    _, trades = await fetch_all_trades()
    holding = [t for t in trades if t.get("status") == "HOLDING"]
    if not holding:
        return

    crypto_data = await build_scanner_data(limit=25)
    price_map = {c["symbol"]: c["price_inr"] for c in crypto_data["coins"]}

    has_nse = any((t.get("market") == "NSE" or ".NS" in str(t.get("symbol", ""))) for t in holding)
    if has_nse:
        nse_data = await build_nse_scanner()
        for s in nse_data["stocks"]:
            price_map[s["short_symbol"]] = s["price_inr"]
            price_map[s["symbol"]] = s["price_inr"]

    for t in holding:
        sym = t.get("symbol", "").upper()
        live_p = price_map.get(sym)
        if not live_p:
            continue
        tp = float(t.get("target_inr") or 0)
        sl = float(t.get("stop_loss_inr") or 0)
        buy = float(t.get("buy_price_inr") or 1)
        budget = float(t.get("budget_inr") or 1000)
        is_nse = (t.get("market") == "NSE" or ".NS" in sym)
        fee_mult = 0.9975 if is_nse else 0.988
        dur_str = format_duration(int(time.time()) - int(t.get("created_at") or time.time()))
        planned = t.get("hold_label") or "1-2 Days"

        if tp > 0 and live_p >= tp:
            await update_trade_status(t["id"], "WIN", live_p, dur_str)
            pnl = budget * ((live_p - buy) / buy) * fee_mult
            await send_telegram_message(
                f"🎯 <b>Target Hit! ({sym})</b>\n"
                f"Time Taken: <b>{dur_str}</b> (Planned: {planned})\n"
                f"Exit: ₹{live_p:,.2f} | Est. Net Profit: <b>+₹{pnl:,.2f}</b>"
            )
        elif sl > 0 and live_p <= sl:
            await update_trade_status(t["id"], "LOSS", live_p, dur_str)
            pnl = budget * ((live_p - buy) / buy)
            await send_telegram_message(
                f"🛑 <b>Stop-Loss Hit ({sym})</b>\n"
                f"Time Taken: <b>{dur_str}</b> (Planned: {planned})\n"
                f"Exit: ₹{live_p:,.2f} | Loss: <b>₹{pnl:,.2f}</b>"
            )


async def run_auto_signal_cycle(min_score: float = 68.0) -> Dict[str, Any]:
    data = await build_scanner_data(limit=25)
    _, existing = await fetch_all_trades()
    holding_syms = {t.get("symbol", "").upper() for t in existing if t.get("status") == "HOLDING"}

    candidates = [
        c for c in data["coins"]
        if c["swing_score"] >= min_score and c["symbol"] not in holding_syms and "Falling Knife" not in c["signal"]
    ][:2]

    logged = []
    for c in candidates:
        row = {
            "id": str(uuid.uuid4())[:8],
            "symbol": c["symbol"],
            "market": "CRYPTO",
            "budget_inr": 1000.0,
            "buy_price_inr": c["price_inr"],
            "target_inr": c["target_inr"],
            "stop_loss_inr": c["stop_loss_inr"],
            "hold_label": c["hold_label"],
            "status": "HOLDING",
            "created_at": int(time.time()),
        }
        await insert_paper_trade(row)
        logged.append(c["symbol"])
        await send_telegram_message(
            f"🟢 <b>CRYPTO AUTO BUY (Paper Logged)</b>\n"
            f"Coin: <b>{c['name']} ({c['symbol']})</b> | Score: <b>{c['swing_score']}</b>\n"
            f"Buy: ₹{c['price_inr']:,.2f} | Hold: <b>{c['hold_label']}</b>\n"
            f"Target: ₹{c['target_inr']:,.2f} | SL: ₹{c['stop_loss_inr']:,.2f}"
        )
    return {"checked": len(data["coins"]), "auto_logged": logged}


async def run_nse_auto_cycle(min_score: float = 70.0) -> Dict[str, Any]:
    data = await build_nse_scanner()
    _, existing = await fetch_all_trades()
    holding_syms = {t.get("symbol", "").upper() for t in existing if t.get("status") == "HOLDING"}

    candidates = [
        s for s in data["stocks"]
        if s["swing_score"] >= min_score and s["short_symbol"] not in holding_syms and "Falling Knife" not in s["signal"]
    ][:2]

    logged = []
    for s in candidates:
        row = {
            "id": str(uuid.uuid4())[:8],
            "symbol": s["short_symbol"],
            "market": "NSE",
            "budget_inr": 5000.0,
            "buy_price_inr": s["price_inr"],
            "target_inr": s["target_inr"],
            "stop_loss_inr": s["stop_loss_inr"],
            "hold_label": s["hold_label"],
            "status": "HOLDING",
            "created_at": int(time.time()),
        }
        await insert_paper_trade(row)
        logged.append(s["short_symbol"])
        await send_telegram_message(
            f"📈 <b>NSE STOCK AUTO BUY (Paper Logged)</b>\n"
            f"Stock: <b>{s['name']} ({s['short_symbol']})</b> | Score: <b>{s['swing_score']}</b>\n"
            f"Buy: ₹{s['price_inr']:,.2f} | Hold: <b>{s['hold_label']}</b>\n"
            f"Target: ₹{s['target_inr']:,.2f} | SL: ₹{s['stop_loss_inr']:,.2f}\n"
            f"Nifty 50: {data['nifty_50']} | VIX: {data['india_vix']}"
        )
    return {"regime": data["regime"], "auto_logged": logged}


async def build_and_send_briefing() -> Dict[str, Any]:
    data = await build_scanner_data(limit=25)
    news = await get_news_and_macro()
    fg = news.get("fear_and_greed", {})
    gem = news.get("gemini_analysis") or {}
    macro_line = gem.get("summary") or "Check panel for latest US Fed & ETF headlines."
    top_picks = [c for c in data["coins"] if c["swing_score"] >= 60][:3]
    picks_txt = "\n".join(
        f"• <b>{c['symbol']}</b>: ₹{c['price_inr']:,.2f} | Score <b>{c['swing_score']}</b> ({c['signal']})"
        for c in top_picks
    ) or "• No safe 60+ setups right now (Capital Protection Mode)."

    msg = (
        f"☀️ <b>ApexQuant 8 AM Crypto Briefing</b>\n"
        f"Fear &amp; Greed: <b>{fg.get('value', '--')} ({fg.get('classification', 'N/A')})</b> | BTC.D: <b>{data['btc_dominance']}%</b>\n\n"
        f"<b>US Macro &amp; News:</b>\n{macro_line}\n\n"
        f"<b>Top Safe INR Swing Picks:</b>\n{picks_txt}"
    )
    ok = await send_telegram_message(msg)
    return {"sent": ok, "picks": len(top_picks)}


async def background_monitor_loop():
    global _LAST_BRIEFING_DATE, _LAST_NSE_SCAN_DATE
    last_scan_ts = 0.0
    while True:
        try:
            await check_open_trades_tp_sl()
            ist_now = datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30)
            today_str = ist_now.strftime("%Y-%m-%d")

            if ist_now.hour == 8 and ist_now.minute < 15 and _LAST_BRIEFING_DATE != today_str:
                _LAST_BRIEFING_DATE = today_str
                await build_and_send_briefing()

            # Mon-Fri 9:30 AM IST NSE Market Open Scan
            if ist_now.weekday() < 5 and ist_now.hour == 9 and 30 <= ist_now.minute < 45 and _LAST_NSE_SCAN_DATE != today_str:
                _LAST_NSE_SCAN_DATE = today_str
                await run_nse_auto_cycle(min_score=70.0)

            if time.time() - last_scan_ts >= 7200:
                last_scan_ts = time.time()
                await run_auto_signal_cycle()
        except Exception:
            pass
        await asyncio.sleep(300)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(background_monitor_loop())
    yield
    task.cancel()


app = FastAPI(title="ApexQuant Dual-Market v5 (Crypto + NSE)", version="5.0.0", lifespan=lifespan)

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
    return {"status": "ApexQuant v5 API running. Visit /docs"}


@app.get("/health")
async def health_check():
    return {"status": "ok", "version": "5.0.0", "ts": int(time.time())}


@app.get("/api/scanner")
async def scan_rising_coins(limit: int = Query(25, ge=5, le=100)):
    return await build_scanner_data(limit=limit)


@app.get("/api/stocks/scanner")
async def get_nse_scanner():
    return await build_nse_scanner()


@app.get("/api/stocks/chart/{symbol}")
async def get_nse_chart(symbol: str):
    sym = symbol if symbol.endswith(".NS") else f"{symbol}.NS"
    cache_key = f"nse_chart_{sym}"
    cached = get_cache(cache_key, ttl_sec=300)
    if cached:
        return cached

    def _fetch():
        t = yf.Ticker(sym)
        hist = t.history(period="4mo", interval="1d")
        if hist.empty:
            return None
        candles = []
        for idx, row in hist.iterrows():
            candles.append({
                "time": int(idx.timestamp()),
                "open": round(float(row["Open"]), 2),
                "high": round(float(row["High"]), 2),
                "low": round(float(row["Low"]), 2),
                "close": round(float(row["Close"]), 2),
            })
        return candles

    candles = await asyncio.to_thread(_fetch)
    if not candles:
        raise HTTPException(status_code=404, detail=f"NSE symbol {sym} not found")
    payload = {"symbol": sym, "candles": candles}
    set_cache(cache_key, payload)
    return payload


@app.get("/api/stocks/news")
async def get_indian_stock_news():
    cached = get_cache("nse_news_v5", ttl_sec=600)
    if cached:
        return cached
    headlines: List[str] = []
    async with httpx.AsyncClient() as client:
        for url in [
            "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
            "https://www.moneycontrol.com/rss/marketreports.xml",
        ]:
            try:
                r = await client.get(url, timeout=8.0)
                if r.status_code == 200:
                    parsed = feedparser.parse(r.text)
                    for e in parsed.entries[:6]:
                        headlines.append(e.title)
            except Exception:
                pass

    ai_summary = None
    if GEMINI_API_KEY and headlines:
        prompt = (
            "You are a pro Indian NSE equity swing analyst. Summarize these headlines focusing on Nifty 50, "
            "RBI/macro, FII/DII flows, and top Indian sectors. Return valid JSON with keys: "
            "macro_bias (Bullish/Bearish/Neutral), summary (2-3 sentences).\n\nHeadlines:\n"
            + "\n".join(f"- {h}" for h in headlines[:8])
        )
        try:
            async with httpx.AsyncClient() as client:
                g_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
                g_resp = await client.post(
                    g_url,
                    json={"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"responseMimeType": "application/json"}},
                    timeout=15.0,
                )
                if g_resp.status_code == 200:
                    ai_summary = json.loads(g_resp.json()["candidates"][0]["content"]["parts"][0]["text"])
        except Exception:
            pass
    elif not GEMINI_API_KEY:
        ai_summary = {"note": "Set GEMINI_API_KEY for Indian market AI summary."}

    payload = {"headlines": headlines[:10], "gemini_analysis": ai_summary}
    set_cache("nse_news_v5", payload)
    return payload


@app.post("/api/alerts/test")
async def test_telegram_alert():
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return {"sent": False, "detail": "Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID"}
    data = await build_scanner_data(limit=15)
    top = data["coins"][0] if data["coins"] else None
    if not top:
        return {"sent": False, "detail": "No coins returned"}
    msg = (
        f"✅ <b>ApexQuant v5 Test Alert</b>\n"
        f"Top Pick: <b>{top['name']} ({top['symbol']})</b>\n"
        f"Price: ₹{top['price_inr']:,.2f} | Score: <b>{top['swing_score']}</b> ({top['signal']})"
    )
    ok = await send_telegram_message(msg)
    return {"sent": ok, "coin": top["symbol"]}


@app.post("/api/alerts/briefing")
async def trigger_morning_briefing():
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return {"sent": False, "detail": "Telegram not configured"}
    return await build_and_send_briefing()


@app.post("/api/alerts/auto-cycle")
async def trigger_auto_cycle():
    return await run_auto_signal_cycle(min_score=65.0)


@app.post("/api/stocks/auto-cycle")
async def trigger_nse_auto_cycle():
    return await run_nse_auto_cycle(min_score=65.0)


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
                fear_greed = {"value": int(fg_data.get("value", 50)), "classification": fg_data.get("value_classification", "Neutral")}
        except Exception:
            fear_greed = {"value": 50, "classification": "Unavailable"}

        for rss_url in ["https://www.coindesk.com/arc/outboundfeeds/rss/", "https://cointelegraph.com/rss"]:
            try:
                r = await client.get(rss_url, timeout=8.0)
                if r.status_code == 200:
                    for entry in feedparser.parse(r.text).entries[:6]:
                        headlines.append(entry.title)
            except Exception:
                pass

    ai_summary = None
    if GEMINI_API_KEY and headlines:
        prompt = (
            "You are a pro crypto spot swing analyst. Summarize these latest headlines focusing on "
            "US Fed/macro policy, ETF flows, and top coin catalysts. Return valid JSON with keys: "
            "macro_bias (Bullish/Bearish/Neutral), summary (2-3 sentences).\n\nHeadlines:\n"
            + "\n".join(f"- {h}" for h in headlines[:10])
        )
        try:
            async with httpx.AsyncClient() as client:
                g_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
                g_resp = await client.post(
                    g_url,
                    json={"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"responseMimeType": "application/json"}},
                    timeout=15.0,
                )
                if g_resp.status_code == 200:
                    ai_summary = json.loads(g_resp.json()["candidates"][0]["content"]["parts"][0]["text"])
        except Exception as exc:
            ai_summary = {"error": f"Gemini analysis failed: {exc}"}
    elif not GEMINI_API_KEY:
        ai_summary = {"note": "Set GEMINI_API_KEY for live AI summary."}

    payload = {"btc_dominance": btc_dom, "fear_and_greed": fear_greed, "headlines": headlines[:10], "gemini_analysis": ai_summary}
    set_cache("news_macro", payload)
    return payload


class PaperTradeIn(BaseModel):
    symbol: str
    market: str = "CRYPTO"
    budget_inr: float
    buy_price_inr: float
    target_inr: float
    stop_loss_inr: float
    hold_label: str = "1-2 Days (Swing)"


@app.get("/api/trades")
async def list_trades():
    storage, trades = await fetch_all_trades()
    return {"storage": storage, "trades": trades}


@app.post("/api/trades")
async def create_trade(trade: PaperTradeIn):
    row = {
        "id": str(uuid.uuid4())[:8],
        "symbol": trade.symbol.upper(),
        "market": trade.market.upper(),
        "budget_inr": round(trade.budget_inr, 2),
        "buy_price_inr": round(trade.buy_price_inr, 2),
        "target_inr": round(trade.target_inr, 2),
        "stop_loss_inr": round(trade.stop_loss_inr, 2),
        "hold_label": trade.hold_label,
        "status": "HOLDING",
        "created_at": int(time.time()),
    }
    storage, saved = await insert_paper_trade(row)
    return {"storage": storage, "trade": saved}


@app.get("/api/trades/export")
async def export_trades_csv():
    _, trades = await fetch_all_trades()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["ID", "Market", "Symbol", "Budget (INR)", "Buy (INR)", "Target (INR)", "SL (INR)", "Hold", "Status", "Duration"])
    for t in trades:
        writer.writerow([
            t.get("id"), t.get("market", "CRYPTO"), t.get("symbol"), t.get("budget_inr"),
            t.get("buy_price_inr"), t.get("target_inr"), t.get("stop_loss_inr"),
            t.get("hold_label"), t.get("status"), t.get("duration_str", "")
        ])
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=apexquant_trades.csv"})
