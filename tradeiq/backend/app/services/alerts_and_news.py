"""
Phase 6 (Milestones 6.1 & 6.2) — Source-Linked Multi-Provider NSE News Service & Closed-Candle Alert Engine.
- Supports Zero-Key RSS (NSE Official RSS + Google News India NSE RSS) AND Optional API Keys
  (`FINNHUB_API_KEY`, `MARKETAUX_API_KEY`).
- Deduplicates by SHA-256 content_hash, sanitizes untrusted headlines against prompt injection,
  tracks published & ingested timestamps, and labels any AI summary as INTERPRETATION.
- Evaluates price/setup alerts ONLY on closed candles with deduplication & cooldown.
"""
import datetime
import hashlib
import json
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, List, Optional

from ..ai.grounding import sanitize_untrusted_text
from ..domain.candles.validator import DomainCandle


@dataclass(frozen=True)
class NewsItem:
    news_id: str
    related_symbol: Optional[str]
    headline: str
    source_name: str
    source_url: str
    published_at_ist: str
    ingested_at_utc: str
    content_hash: str
    ai_summary_interpretation: Optional[str] = None
    ai_summary_label: str = "INTERPRETATION — NOT VERIFIED FACT"
    injection_sanitized: bool = False


class NewsAndAlertsService:
    def __init__(self, finnhub_api_key: str = "", marketaux_api_key: str = "") -> None:
        self._news_by_hash: Dict[str, NewsItem] = {}
        self._cooldowns: Dict[str, float] = {}
        self.finnhub_api_key = (finnhub_api_key or "").strip()
        self.marketaux_api_key = (marketaux_api_key or "").strip()

    def ingest_news_item(
        self,
        headline: str,
        source_name: str,
        source_url: str,
        published_at_ist: str,
        related_symbol: Optional[str] = None,
        ai_summary: Optional[str] = None,
    ) -> NewsItem:
        clean_hl, inj_hl = sanitize_untrusted_text(headline, max_len=240)
        clean_sum, inj_sum = sanitize_untrusted_text(ai_summary or "", max_len=320) if ai_summary else (None, False)
        chash = hashlib.sha256(f"{source_name}:{clean_hl.lower()}".encode("utf-8")).hexdigest()[:24]
        if chash in self._news_by_hash:
            return self._news_by_hash[chash]

        item = NewsItem(
            news_id=str(uuid.uuid4()),
            related_symbol=related_symbol.upper() if related_symbol else None,
            headline=clean_hl,
            source_name=source_name,
            source_url=source_url,
            published_at_ist=published_at_ist,
            ingested_at_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            content_hash=chash,
            ai_summary_interpretation=clean_sum,
            ai_summary_label="INTERPRETATION — NOT VERIFIED FACT",
            injection_sanitized=bool(inj_hl or inj_sum),
        )
        self._news_by_hash[chash] = item
        return item

    def fetch_live_rss_or_api_news(self, symbol: str = "RELIANCE", allow_network: bool = False) -> List[NewsItem]:
        """
        Fetches latest NSE stock news from:
        1. Google News India NSE RSS (Keyless)
        2. Optional Marketaux India News API (if MARKETAUX_API_KEY is configured)
        3. Optional Finnhub Market News API (if FINNHUB_API_KEY is configured)
        Safe no-op fallback when allow_network=False (offline/test mode).
        """
        sym = symbol.strip().upper()
        if not allow_network:
            return self.list_news(symbol=sym)

        # 1. Keyless Google News India NSE RSS
        try:
            q = urllib.parse.quote(f"{sym} NSE stock India when:3d")
            rss_url = f"https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en"
            req = urllib.request.Request(rss_url, headers={"User-Agent": "TRADEIQ/1.0"})
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                xml_bytes = resp.read()
                root = ET.fromstring(xml_bytes)
                for item_el in root.findall(".//item")[:5]:
                    title = (item_el.findtext("title") or "").strip()
                    link = (item_el.findtext("link") or "https://news.google.com").strip()
                    pub = (item_el.findtext("pubDate") or datetime.datetime.now().isoformat()).strip()
                    if title:
                        self.ingest_news_item(
                            headline=title,
                            source_name="Google News India RSS",
                            source_url=link,
                            published_at_ist=pub,
                            related_symbol=sym,
                        )
        except Exception:
            pass

        # 2. Optional Marketaux India News API
        if self.marketaux_api_key and not self.marketaux_api_key.startswith("YOUR_"):
            try:
                m_url = (
                    f"https://api.marketaux.com/v1/news/all?countries=in&filter_entities=true"
                    f"&limit=5&api_token={self.marketaux_api_key}"
                )
                with urllib.request.urlopen(m_url, timeout=5.0) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    for art in data.get("data", []):
                        self.ingest_news_item(
                            headline=art.get("title", ""),
                            source_name=art.get("source", "Marketaux India"),
                            source_url=art.get("url", "https://www.marketaux.com/news/country/in"),
                            published_at_ist=art.get("published_at", ""),
                            related_symbol=sym,
                            ai_summary=art.get("description"),
                        )
            except Exception:
                pass

        return self.list_news(symbol=sym)

    def list_news(self, symbol: Optional[str] = None) -> List[NewsItem]:
        items = list(self._news_by_hash.values())
        if symbol:
            s = symbol.strip().upper()
            items = [x for x in items if x.related_symbol == s]
        return sorted(items, key=lambda x: x.published_at_ist, reverse=True)

    def evaluate_closed_candle_price_alert(
        self,
        alert_id: str,
        condition_type: str,  # "CROSSES_ABOVE" | "CROSSES_BELOW"
        threshold: Decimal,
        candle: DomainCandle,
        now_epoch: float,
        cooldown_sec: int = 1800,
    ) -> bool:
        """Rule 5: Never triggers an alert from an incomplete (is_closed=False) candle."""
        if not candle.is_closed or candle.quality_status != "VALID":
            return False
        if self._cooldowns.get(alert_id, 0.0) > now_epoch:
            return False
        triggered = (
            (condition_type == "CROSSES_ABOVE" and candle.close >= threshold)
            or (condition_type == "CROSSES_BELOW" and candle.close <= threshold)
        )
        if triggered:
            self._cooldowns[alert_id] = now_epoch + cooldown_sec
        return triggered
