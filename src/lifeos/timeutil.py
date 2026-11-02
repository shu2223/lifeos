"""Time handling.

Every timestamp keeps the UTC offset it happened in, so a meal eaten in Shanghai stays at its
local clock time. `local_date` is the calendar day in that offset and is what daily views use.
"""
from datetime import UTC, date, datetime, time, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_TZ = "Asia/Tokyo"


@lru_cache(maxsize=64)
def zone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or DEFAULT_TZ)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(DEFAULT_TZ)


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def now(tz: ZoneInfo) -> datetime:
    return datetime.now(tz).replace(microsecond=0)


def parse(value: str | None, tz: ZoneInfo) -> datetime:
    """Accept ISO 8601 (with or without offset), Apple's "2024-01-01 10:00:00 +0900",
    a bare date (12:00 that day) or a bare clock time like "12:30" (today).
    Naive times are in `tz`. None means now."""
    if value is None or not str(value).strip():
        return now(tz)
    v = str(value).strip()
    if len(v) == 10:
        return datetime.combine(date.fromisoformat(v), time(12, 0), tz)
    if len(v) in (4, 5, 8) and v[-3] == ":" and v.replace(":", "").isdigit():
        try:
            return datetime.combine(now(tz).date(), time.fromisoformat(v.zfill(5)), tz)
        except ValueError:
            pass
    try:
        dt = datetime.fromisoformat(v)
    except ValueError:
        try:
            dt = datetime.strptime(v, "%Y-%m-%d %H:%M:%S %z")
        except ValueError:
            raise ValueError(
                f"unrecognised time {v!r}; use ISO 8601 like 2026-11-02T12:30:00+09:00"
            ) from None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt.replace(microsecond=0)


def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def parse_day(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"unrecognised date {value!r}; use YYYY-MM-DD") from None


def day_bounds(d: date, tz: ZoneInfo) -> tuple[float, float]:
    start = datetime.combine(d, time(0), tz)
    return start.timestamp(), (start + timedelta(days=1)).timestamp()


_OFFSETS: dict[str, timezone] = {}


def apple(s: str) -> tuple[str, float, str]:
    """Fast path for Apple's "2024-01-01 10:00:00 +0900": (iso string, unix ts, local date).
    Millions of samples go through here during an export import."""
    off = s[20:25]
    tz = _OFFSETS.get(off)
    if tz is None:
        sign = -1 if off[0] == "-" else 1
        tz = timezone(sign * timedelta(hours=int(off[1:3]), minutes=int(off[3:5])))
        _OFFSETS[off] = tz
    dt = datetime(int(s[0:4]), int(s[5:7]), int(s[8:10]),
                  int(s[11:13]), int(s[14:16]), int(s[17:19]), tzinfo=tz)
    return f"{s[:10]}T{s[11:19]}{off[:3]}:{off[3:]}", dt.timestamp(), s[:10]
