"""Facts about the user that are not tied to a moment: name, birthday, height, goals, timezone."""
from contextlib import nullcontext

from .db import dumps, loads, log_change, tx
from .errors import bad_request
from .timeutil import DEFAULT_TZ, utc_now, zone

DEFAULTS = {"timezone": DEFAULT_TZ}
RECOMMENDED_KEYS = {
    "name": "what to call the user",
    "birthday": "YYYY-MM-DD",
    "sex": "as the user describes it",
    "height_cm": "number",
    "timezone": "IANA name, e.g. Asia/Tokyo; naive times and daily views use it",
    "currency": "default ISO 4217 code for expenses, e.g. JPY",
    "goals": "object, e.g. {kcal, protein_g, carbs_g, fat_g, steps, sleep_hours, water_ml, "
             "weight_kg, exercise_min}",
    "diet": "object: {preferences, allergies, avoid} - respected when estimating meals",
    "notes": "anything else agents should know about the user",
}


def get_all(conn) -> dict:
    out = dict(DEFAULTS)
    for r in conn.execute("SELECT key, value FROM profile ORDER BY key"):
        out[r["key"]] = loads(r["value"])
    return out


def timezone_of(conn):
    r = conn.execute("SELECT value FROM profile WHERE key = 'timezone'").fetchone()
    return zone(loads(r["value"]) if r else None)


def _check(key: str, value) -> None:
    if not key or len(key) > 64:
        raise bad_request("profile keys must be 1-64 characters")
    if key == "timezone":
        if not isinstance(value, str) or zone(value).key != value:
            raise bad_request(f"unknown timezone {value!r}", "use an IANA name like Asia/Tokyo")
    if key == "goals" and (not isinstance(value, dict) or not all(
            isinstance(v, (int, float)) or v is None for v in value.values())):
        raise bad_request("goals must be an object of numbers", 'e.g. {"kcal": 2000}')


def patch(conn, body: dict, actor: str, only_missing: bool = False, in_tx: bool = False) -> dict:
    """Merge keys into the profile; a null value deletes the key. `only_missing` leaves keys
    the user already set alone (imports use it)."""
    if not isinstance(body, dict) or not body:
        raise bad_request("send a JSON object of profile keys")
    for k, v in body.items():
        if v is not None:
            _check(k, v)
    now = utc_now()
    with nullcontext() if in_tx else tx(conn):
        before = get_all(conn)
        for k, v in body.items():
            if only_missing and k in before and k not in DEFAULTS:
                continue
            if v is None:
                conn.execute("DELETE FROM profile WHERE key = ?", (k,))
            else:
                conn.execute(
                    "INSERT INTO profile(key, value, updated_by, updated_at) VALUES (?, ?, ?, ?)"
                    " ON CONFLICT(key) DO UPDATE SET value = excluded.value,"
                    " updated_by = excluded.updated_by, updated_at = excluded.updated_at",
                    (k, dumps(v), actor, now))
        after = get_all(conn)
        changed = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
        if changed:
            log_change(conn, actor, "update", "profile", "profile",
                       before={k: before.get(k) for k in changed},
                       after={k: after.get(k) for k in changed}, at=now)
    return after
