"""Session display timezone helpers (UTC storage, PDT/IST display)."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Optional, Union
from zoneinfo import ZoneInfo

from starlette.requests import Request

TIMEZONES = {
    "PDT": "America/Los_Angeles",
    "IST": "Asia/Kolkata",
}
DEFAULT_TZ = "PDT"
COOKIE_NAME = "pla_tz"
COOKIE_MAX_AGE = 60 * 60 * 24 * 365


def normalize_tz_key(value: Optional[str]) -> str:
    key = (value or "").strip().upper()
    if key in TIMEZONES:
        return key
    return DEFAULT_TZ


def zone_info(tz_key: Optional[str] = None) -> ZoneInfo:
    return ZoneInfo(TIMEZONES[normalize_tz_key(tz_key)])


def get_tz_key(request: Optional[Request] = None) -> str:
    if request is None:
        return DEFAULT_TZ
    return normalize_tz_key(request.cookies.get(COOKIE_NAME))


def utc_now() -> datetime:
    """Naive UTC datetime for storage in DateTime columns."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def now(request: Optional[Request] = None, tz_key: Optional[str] = None) -> datetime:
    key = normalize_tz_key(tz_key if tz_key is not None else get_tz_key(request))
    return datetime.now(zone_info(key))


def today(request: Optional[Request] = None, tz_key: Optional[str] = None) -> date:
    return now(request, tz_key).date()


def from_utc(
    dt: Optional[datetime],
    tz_key: Optional[str] = None,
) -> Optional[datetime]:
    """Convert a naive UTC (or aware) datetime into the display zone."""
    if dt is None:
        return None
    key = normalize_tz_key(tz_key)
    if dt.tzinfo is None:
        aware = dt.replace(tzinfo=timezone.utc)
    else:
        aware = dt
    return aware.astimezone(zone_info(key))


def to_utc(dt: datetime, tz_key: Optional[str] = None) -> datetime:
    """Interpret a naive local datetime as the display zone and return naive UTC."""
    key = normalize_tz_key(tz_key)
    z = zone_info(key)
    if dt.tzinfo is None:
        local = dt.replace(tzinfo=z)
    else:
        local = dt.astimezone(z)
    return local.astimezone(timezone.utc).replace(tzinfo=None)


def day_bounds_utc(
    day: date,
    tz_key: Optional[str] = None,
) -> tuple[datetime, datetime]:
    """UTC [start, end) covering a local calendar day."""
    key = normalize_tz_key(tz_key)
    start_local = datetime.combine(day, time.min, tzinfo=zone_info(key))
    end_local = datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone_info(key))
    return (
        start_local.astimezone(timezone.utc).replace(tzinfo=None),
        end_local.astimezone(timezone.utc).replace(tzinfo=None),
    )


def month_bounds_utc(
    year: int,
    month: int,
    tz_key: Optional[str] = None,
) -> tuple[datetime, datetime]:
    """UTC [start, end) for a calendar month in the display zone."""
    key = normalize_tz_key(tz_key)
    z = zone_info(key)
    start_local = datetime(year, month, 1, tzinfo=z)
    if month == 12:
        end_local = datetime(year + 1, 1, 1, tzinfo=z)
    else:
        end_local = datetime(year, month + 1, 1, tzinfo=z)
    return (
        start_local.astimezone(timezone.utc).replace(tzinfo=None),
        end_local.astimezone(timezone.utc).replace(tzinfo=None),
    )


def format_dt(
    dt: Optional[Union[datetime, date]],
    tz_key: Optional[str] = None,
    *,
    empty: str = "—",
) -> str:
    if dt is None:
        return empty
    if isinstance(dt, datetime):
        local = from_utc(dt, tz_key)
        if local is None:
            return empty
        return local.strftime("%Y-%m-%d %I:%M %p %Z")
    return dt.isoformat()
