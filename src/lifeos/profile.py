"""Facts about the user that are not tied to a moment: name, birthday, height, goals, timezone."""
from contextlib import nullcontext

from .categories import NUTRIENT_ALIASES, NUTRIENTS
from .db import dumps, loads, log_change, tx
from .errors import bad_request
from .timeutil import DEFAULT_TZ, utc_now, zone

DEFAULTS = {"timezone": DEFAULT_TZ}
# The goals something reads: /summary/day the nutrients and cigarettes_max, the web page the rest.
GOAL_KEYS = (*NUTRIENTS, "steps", "exercise_min", "sleep_hours", "weight_kg", "cigarettes_max")
# Names agents write for them, matched without case. A goal under any other name would be stored
# and never read, and PATCH /profile has no warnings to say so, so it is refused.
GOAL_ALIASES = {
    **NUTRIENT_ALIASES,
    "step": "steps", "step_count": "steps", "daily_steps": "steps", "steps_per_day": "steps",
    "exercise_minutes": "exercise_min", "exercise_time": "exercise_min",
    "sleep_h": "sleep_hours", "sleep_hour": "sleep_hours", "sleep_hrs": "sleep_hours",
    "weight": "weight_kg", "target_weight": "weight_kg", "target_weight_kg": "weight_kg",
    "goal_weight": "weight_kg", "body_weight": "weight_kg", "body_mass": "weight_kg",
    "max_cigarettes": "cigarettes_max", "cigarettes": "cigarettes_max",
    "cigarette_max": "cigarettes_max", "cigarettes_per_day": "cigarettes_max",
    "cigarettes_limit": "cigarettes_max", "max_cigarettes_per_day": "cigarettes_max",
}
RECOMMENDED_KEYS = {
    "name": "what to call the user",
    "birthday": "YYYY-MM-DD",
    "sex": "as the user describes it",
    "height_cm": "number",
    "timezone": "IANA name, e.g. Asia/Tokyo; naive times and daily views use it",
    "currency": "default ISO 4217 code for expenses, e.g. JPY",
    "goals": f"object of numbers under these keys: {', '.join(GOAL_KEYS)}",
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


def canonical_goals(goals: dict) -> dict:
    """`goals` under the keys their readers use: aliases renamed, a key already given winning
    over its alias. A null under an unknown name passes, so such a goal can be removed."""
    out = {k: v for k, v in goals.items() if k in GOAL_KEYS}
    for key, value in goals.items():
        if key in out:
            continue
        low = key.lower()
        to = low if low in GOAL_KEYS else GOAL_ALIASES.get(low)
        if to is not None:
            out.setdefault(to, value)
        elif value is None:
            out[key] = None
        else:
            raise bad_request(f"unknown goal {key!r}: nothing reads it",
                              f"goals are {', '.join(GOAL_KEYS)}")
    return out


def patch(conn, body: dict, actor: str, only_missing: bool = False, in_tx: bool = False) -> dict:
    """Merge keys into the profile; a null value deletes the key. `goals` merges one level down,
    so an agent can set one goal without erasing the rest; a null goal removes that goal.
    `only_missing` leaves keys the user already set alone (imports use it)."""
    if not isinstance(body, dict) or not body:
        raise bad_request("send a JSON object of profile keys")
    for k, v in body.items():
        if v is not None:
            _check(k, v)
    if body.get("goals"):
        body = {**body, "goals": canonical_goals(body["goals"])}
    now = utc_now()
    with nullcontext() if in_tx else tx(conn):
        before = get_all(conn)
        for k, v in body.items():
            if only_missing and k in before and k not in DEFAULTS:
                continue
            if k == "goals" and v is not None:
                v = {g: n for g, n in {**(before.get(k) or {}), **v}.items() if n is not None} or None
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
