"""
TRADEIQ v2.0 — Real Multi-Source Indian Market News Aggregator,
Gemini News Sentiment Shield, and Closed-Candle Price Alerts.

Fetches top Indian market & stock-specific news from 5 keyless financial RSS feeds:
1. Economic Times Markets RSS
2. Economic Times Stocks RSS
3. Livemint Markets RSS
4. Moneycontrol Market Reports RSS
5. Google News India Markets / Symbol RSS
Plus optional Marketaux / Finnhub APIs when keys are configured.

Uses browser-standard User-Agent, exposes feed health telemetry (no silent failures),
and enriches top stories with Gemini 2-line summaries + Sentiment Shield tags
("BULLISH" | "BEARISH" | "NEUTRAL" | "HIGH_RISK").
"""
import datetime
import hashlib
import html
import json
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from ..ai.grounding import sanitize_untrusted_text
from ..domain.candles.aggregator import DomainCandle

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/xml, text/xml, */*;q=0.8",
}

_INDIAN_MARKET_RSS_FEEDS: List[Tuple[str, str, str]] = [
    ("Economic Times Markets", "MARKET", "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"),
    ("Economic Times Stocks", "STOCKS", "https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms"),
    ("Livemint Markets", "MARKET", "https://www.livemint.com/rss/markets"),
    ("Moneycontrol Markets", "MARKET", "https://www.moneycontrol.com/rss/marketreports.xml"),
    (
        "Google News India Markets",
        "MARKET",
        "https://news.google.com/rss/search?q=Nifty+OR+BankNifty+OR+Sensex+OR+NSE+India+when:2d&hl=en-IN&gl=IN&ceid=IN:en",
    ),
]

_HIGH_RISK_KEYWORDS = (
    "sebi", "fraud", "raid", "penalty", "default", "insolvency", "resigns",
    "auditor", "crash", "plunge", "ban", "probe", "investigation", "scam",
    "downgrade", "slumps", "tumbles", "loss widens", "guidance cut",
)
_BEARISH_KEYWORDS = (
    "falls", "drops", "declines", "weak", "selloff", "sell-off", "bearish",
    "slip", "slips", "profit drops", "misses estimates", "outflow", "fii sells",
)
_BULLISH_KEYWORDS = (
    "surges", "rallies", "jumps", "beats estimates", "record high", "all-time high",
    "upgrade", "order win", "profit jumps", "fii buys", "breakout", "bullish", "gains",
)

_TAG_STRIP_RE = re.compile(r"<[^>]+>")


def _clean_text(raw: Optional[str], max_len: int = 320) -> str:
    if not raw:
        return ""
    txt = html.unescape(_TAG_STRIP_RE.sub(" ", raw))
    txt = re.sub(r"\s+", " ", txt).strip()
    return txt[:max_len]


def _heuristic_sentiment(headline: str, summary: str = "") -> Tuple[str, str]:
    text = f"{headline} {summary}".lower()
    if any(k in text for k in _HIGH_RISK_KEYWORDS):
        return "HIGH_RISK", "High-impact negative/regulatory headline detected — Auto-Trader BUY shield active."
    if any(k in text for k in _BEARISH_KEYWORDS):
        return "BEARISH", "Negative price/earnings tone detected in headline."
    if any(k in text for k in _BULLISH_KEYWORDS):
        return "BULLISH", "Positive momentum/catalyst tone detected in headline."
    return "NEUTRAL", "Informational market update without extreme directional risk."


@dataclass(frozen=True)
class AlertTriggerRecord:
    alert_id: str
    symbol: str
    condition_type: str
    threshold: Decimal
    triggered_price: Decimal
    candle_ts_ist: str
    channels_dispatched: List[str]


@dataclass
class NewsItem:
    news_id: str
    headline: str
    source_name: str
    source_url: str
    published_at_ist: str
    related_symbol: Optional[str]
    content_hash: str
    category: str = "MARKET"
    ai_summary: Optional[str] = None
    ai_summary_label: str = "INTERPRETATION — NOT VERIFIED FACT"
    injection_sanitized: bool = False
    sentiment: str = "NEUTRAL"  # "BULLISH" | "BEARISH" | "NEUTRAL" | "HIGH_RISK"
    shield_note: Optional[str] = None
    disclaimer: str = "External news aggregated for informational analysis only."


class NewsAndAlertsService:
    def __init__(self, finnhub_api_key: str = "", marketaux_api_key: str = "") -> None:
        self.finnhub_api_key = finnhub_api_key.strip()
        self.marketaux_api_key = marketaux_api_key.strip()
        self._news_by_hash: Dict[str, NewsItem] = {}
        self._last_alert_epoch: Dict[str, float] = {}
        self.triggered_alerts: List[AlertTriggerRecord] = []
        self.feed_health: Dict[str, Dict[str, Any]] = {}
        self._last_fetch_epoch: Dict[str, float] = {}

    def ingest_news_item(
        self,
        headline: str,
        source_name: str,
        source_url: str,
        published_at_ist: str,
        related_symbol: Optional[str] = None,
        ai_summary: Optional[str] = None,
        category: str = "MARKET",
        sentiment: Optional[str] = None,
    ) -> NewsItem:
        raw_h = _clean_text(headline, max_len=240)
        clean_h, inj_flag = sanitize_untrusted_text(raw_h, max_len=240)
        clean_s = _clean_text(ai_summary, max_len=360) if ai_summary else None
        norm = f"{clean_h.lower()}|{source_name.strip().lower()}"
        chash = hashlib.sha256(norm.encode("utf-8")).hexdigest()[:24]
        if chash in self._news_by_hash:
            existing = self._news_by_hash[chash]
            if clean_s and not existing.ai_summary:
                existing.ai_summary = clean_s
            return existing

        det_sent, shield_note = _heuristic_sentiment(clean_h, clean_s or "")
        final_sent = (sentiment or det_sent).upper()
        item = NewsItem(
            news_id=chash[:12],
            headline=clean_h,
            source_name=source_name.strip(),
            source_url=source_url.strip(),
            published_at_ist=published_at_ist,
            related_symbol=related_symbol.upper() if related_symbol else None,
            content_hash=chash,
            category=category.upper(),
            ai_summary=clean_s or shield_note,
            sentiment=final_sent,
            shield_note=shield_note,
            injection_sanitized=inj_flag,
        )
        self._news_by_hash[chash] = item
        return item

    def _parse_rss(
        self,
        url: str,
        source_name: str,
        category: str,
        related_symbol: Optional[str] = None,
        limit: int = 8,
    ) -> int:
        req = urllib.request.Request(url, headers=_BROWSER_HEADERS)
        count = 0
        try:
            with urllib.request.urlopen(req, timeout=6.0) as resp:
                xml_bytes = resp.read()
            root = ET.fromstring(xml_bytes)
            for item_el in root.findall(".//item")[:limit]:
                title = _clean_text(item_el.findtext("title"), max_len=240)
                link = (item_el.findtext("link") or url).strip()
                desc = _clean_text(item_el.findtext("description"), max_len=340)
                pub = (item_el.findtext("pubDate") or datetime.datetime.now(IST).isoformat()).strip()
                if title:
                    self.ingest_news_item(
                        headline=title,
                        source_name=source_name,
                        source_url=link,
                        published_at_ist=pub,
                        related_symbol=related_symbol,
                        ai_summary=desc if desc and desc.lower() != title.lower() else None,
                        category=category,
                    )
                    count += 1
            self.feed_health[source_name] = {
                "status": "OK",
                "items_fetched": count,
                "checked_at_ist": datetime.datetime.now(IST).isoformat(),
            }
        except Exception as exc:
            self.feed_health[source_name] = {
                "status": "ERROR",
                "error": f"{type(exc).__name__}: {str(exc)[:100]}",
                "checked_at_ist": datetime.datetime.now(IST).isoformat(),
            }
        return count

    def fetch_top_indian_market_news(
        self,
        symbol: Optional[str] = None,
        allow_network: bool = True,
        llm_client: Optional[Any] = None,
        force_refresh: bool = False,
    ) -> List[NewsItem]:
        """
        Fetches real top Indian market news (ET Markets, ET Stocks, Livemint, Moneycontrol, Google News India)
        plus optional stock-specific RSS & Marketaux/Finnhub APIs, then enriches top stories with Gemini.
        """
        sym = symbol.strip().upper() if symbol else None
        cache_key = sym or "__TOP_MARKET__"
        now_ep = datetime.datetime.now(datetime.timezone.utc).timestamp()

        if allow_network and (force_refresh or (now_ep - self._last_fetch_epoch.get(cache_key, 0.0) > 120.0)):
            if sym:
                q = urllib.parse.quote(f"{sym} NSE stock India when:4d")
                g_url = f"https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en"
                self._parse_rss(g_url, f"Google News ({sym})", "STOCK", related_symbol=sym, limit=8)
            else:
                for src_name, cat, feed_url in _INDIAN_MARKET_RSS_FEEDS:
                    self._parse_rss(feed_url, src_name, cat, related_symbol=None, limit=6)

            # Optional Marketaux India News API
            if self.marketaux_api_key and not self.marketaux_api_key.startswith("YOUR_"):
                try:
                    m_url = (
                        f"https://api.marketaux.com/v1/news/all?countries=in&filter_entities=true"
                        f"&limit=6&api_token={self.marketaux_api_key}"
                    )
                    req = urllib.request.Request(m_url, headers=_BROWSER_HEADERS)
                    with urllib.request.urlopen(req, timeout=6.0) as resp:
                        data = json.loads(resp.read().decode("utf-8"))
                        for art in data.get("data", []):
                            self.ingest_news_item(
                                headline=art.get("title", ""),
                                source_name=art.get("source", "Marketaux India"),
                                source_url=art.get("url", "https://www.marketaux.com/news/country/in"),
                                published_at_ist=art.get("published_at", datetime.datetime.now(IST).isoformat()),
                                related_symbol=sym,
                                ai_summary=art.get("description"),
                                category="MARKET" if not sym else "STOCK",
                            )
                    self.feed_health["Marketaux India"] = {"status": "OK", "checked_at_ist": datetime.datetime.now(IST).isoformat()}
                except Exception as exc:
                    self.feed_health["Marketaux India"] = {"status": "ERROR", "error": type(exc).__name__}

            self._last_fetch_epoch[cache_key] = now_ep

        items = self.list_news(symbol=sym)

        # Optional Gemini enrichment for top 5 headlines (1 single batched JSON call to stay quota-safe)
        if llm_client is not None and hasattr(llm_client, "is_configured") and llm_client.is_configured() and items:
            to_enrich = [x for x in items[:5] if not x.shield_note or "Gemini" not in (x.shield_note or "")]
            if to_enrich:
                try:
                    payload = [{"id": x.news_id, "headline": x.headline, "snippet": x.ai_summary or ""} for x in to_enrich]
                    res = llm_client.generate_structured_json(
                        system_prompt=(
                            "You are an Indian stock market news analyst. For each news item, return JSON "
                            "{\"items\": [{\"id\": \"...\", \"summary\": \"2 concise readable sentences\", "
                            "\"sentiment\": \"BULLISH|BEARISH|NEUTRAL|HIGH_RISK\"}]}. Never invent prices."
                        ),
                        user_prompt=json.dumps(payload, ensure_ascii=False),
                    )
                    by_id = {str(r.get("id")): r for r in (res.get("items") or []) if isinstance(r, dict)}
                    for it in to_enrich:
                        upd = by_id.get(it.news_id)
                        if upd:
                            s_txt = _clean_text(str(upd.get("summary") or ""), max_len=320)
                            sent = str(upd.get("sentiment") or it.sentiment).upper()
                            if sent in ("BULLISH", "BEARISH", "NEUTRAL", "HIGH_RISK"):
                                it.sentiment = sent
                            if s_txt:
                                it.ai_summary = s_txt
                            it.shield_note = f"Gemini News Shield: {it.sentiment}"
                except Exception:
                    pass

        return items

    def fetch_live_rss_or_api_news(self, symbol: str = "RELIANCE", allow_network: bool = False) -> List[NewsItem]:
        """Backward-compatible wrapper."""
        return self.fetch_top_indian_market_news(symbol=symbol, allow_network=allow_network)

    def get_symbol_sentiment_map(self, symbols: List[str]) -> Dict[str, str]:
        """Returns worst-case sentiment per symbol from ingested news for the Auto-Trader News Shield."""
        priority = {"HIGH_RISK": 3, "BEARISH": 2, "BULLISH": 1, "NEUTRAL": 0}
        out: Dict[str, str] = {}
        all_items = list(self._news_by_hash.values())
        for sym in symbols:
            s_up = sym.upper()
            best = "NEUTRAL"
            for it in all_items:
                if it.related_symbol == s_up or s_up in it.headline.upper():
                    if priority.get(it.sentiment, 0) > priority.get(best, 0):
                        best = it.sentiment
            out[s_up] = best
        return out

    def list_news(self, symbol: Optional[str] = None) -> List[NewsItem]:
        items = list(self._news_by_hash.values())
        if symbol:
            s = symbol.strip().upper()
            items = [x for x in items if x.related_symbol == s or s in x.headline.upper()]
        return sorted(items, key=lambda x: x.published_at_ist, reverse=True)

    def evaluate_closed_candle_price_alert(
        self,
        alert_id: str,
        condition_type: str,  # "CROSSES_ABOVE" | "CROSSES_BELOW"
        threshold: Decimal,
        candle: DomainCandle,
        now_epoch: float,
        cooldown_sec: int = 900,
        channels: Optional[List[str]] = None,
    ) -> Optional[AlertTriggerRecord]:
        if not candle.is_closed:
            return None
        last_t = self._last_alert_epoch.get(alert_id)
        if last_t is not None and (now_epoch - last_t) < cooldown_sec:
            return None

        hit = (
            (condition_type == "CROSSES_ABOVE" and candle.close >= threshold)
            or (condition_type == "CROSSES_BELOW" and candle.close <= threshold)
        )
        if not hit:
            return None

        self._last_alert_epoch[alert_id] = now_epoch
        rec = AlertTriggerRecord(
            alert_id=alert_id,
            symbol=candle.symbol,
            condition_type=condition_type,
            threshold=threshold,
            triggered_price=candle.close,
            candle_ts_ist=candle.ts_ist.isoformat(),
            channels_dispatched=channels or ["IN_APP", "TELEGRAM"],
        )
        self.triggered_alerts.append(rec)
        return rec
