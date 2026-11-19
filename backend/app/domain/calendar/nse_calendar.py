"""
Milestone 2.1 — Pure Timezone-Safe NSE Calendar, Session Windows & Freshness Evaluator.
Independent of server OS timezone; all calculations anchor to Asia/Kolkata (UTC+05:30).
"""
import datetime
from typing import Optional, Set

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

PRE_MARKET_START = datetime.time(9, 0, 0)
SESSION_OPEN = datetime.time(9, 15, 0)
SESSION_CLOSE = datetime.time(15, 30, 0)

DEFAULT_NSE_HOLIDAYS_2026: Set[datetime.date] = {
    datetime.date(2026, 1, 26),   # Republic Day
    datetime.date(2026, 3, 3),    # Holi
    datetime.date(2026, 4, 3),    # Good Friday
    datetime.date(2026, 4, 14),   # Dr. Baba Saheb Ambedkar Jayanti
    datetime.date(2026, 5, 1),    # Maharashtra Day
    datetime.date(2026, 8, 15),   # Independence Day
    datetime.date(2026, 10, 2),   # Mahatma Gandhi Jayanti
    datetime.date(2026, 11, 9),   # Diwali Laxmi Pujan
    datetime.date(2026, 11, 10),  # Diwali Balipratipada
    datetime.date(2026, 11, 24),  # Gurunanak Jayanti
    datetime.date(2026, 12, 25),  # Christmas
}


def to_ist(dt: datetime.datetime) -> datetime.datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(IST)


def is_trading_day(d: datetime.date, holidays: Optional[Set[datetime.date]] = None) -> bool:
    hols = holidays if holidays is not None else DEFAULT_NSE_HOLIDAYS_2026
    if d.weekday() >= 5:
        return False
    return d not in hols


def get_market_state(
    now_dt: datetime.datetime,
    holidays: Optional[Set[datetime.date]] = None,
    provider_available: bool = True,
) -> str:
    """Returns PRE_MARKET | OPEN | CLOSED | HOLIDAY | UNAVAILABLE."""
    if not provider_available:
        return "UNAVAILABLE"
    ist_dt = to_ist(now_dt)
    d = ist_dt.date()
    hols = holidays if holidays is not None else DEFAULT_NSE_HOLIDAYS_2026
    if d in hols or d.weekday() >= 5:
        return "HOLIDAY"
    t = ist_dt.time()
    if PRE_MARKET_START <= t < SESSION_OPEN:
        return "PRE_MARKET"
    if SESSION_OPEN <= t < SESSION_CLOSE:
        return "OPEN"
    return "CLOSED"


def evaluate_freshness(
    exchange_ts: Optional[datetime.datetime],
    now_dt: datetime.datetime,
    base_class: str = "LIVE",
    max_live_age_seconds: int = 90,
) -> str:
    """
    Computes effective freshness class (Rule 3 & Rule 4 of Section 7):
    LIVE, DELAYED, HISTORICAL, SIMULATED, STALE, UNAVAILABLE.
    """
    if base_class == "UNAVAILABLE" or exchange_ts is None:
        return "UNAVAILABLE"
    if base_class == "SIMULATED":
        return "SIMULATED"
    age = (to_ist(now_dt) - to_ist(exchange_ts)).total_seconds()
    if base_class == "LIVE" and age > max_live_age_seconds:
        return "STALE"
    return base_class
