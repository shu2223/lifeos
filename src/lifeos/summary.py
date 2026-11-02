"""Daily and multi-day views that answer "how am I doing today" in one request."""
from datetime import date, timedelta

from . import profile, records, samples
from .categories import NUTRIENTS
from .db import loads
from .errors import bad_request
from .metrics import SLEEP_ASLEEP

DAY_METRICS = {
    "steps": "step_count",
    "distance_km": "walking_running_distance",
    "flights_climbed": "flights_climbed",
    "active_kcal": "active_energy",
    "basal_kcal": "basal_energy",
    "exercise_min": "exercise_time",
    "stand_hours": "stand_hour",
    "daylight_min": "time_in_daylight",
    "mindful_min": "mindful_minutes",
    "water_ml": "dietary_water",
    "resting_hr": "resting_heart_rate",
    "avg_hr": "heart_rate",
    "hrv_ms": "heart_rate_variability",
    "spo2_pct": "oxygen_saturation",
    "respiratory_rate": "respiratory_rate",
}
SLEEP_METRICS = {"in_bed_min": "sleep_in_bed", "asleep_unstaged_min": "sleep_asleep",
                 "core_min": "sleep_core", "deep_min": "sleep_deep", "rem_min": "sleep_rem",
                 "awake_min": "sleep_awake"}
MAX_RANGE_DAYS = 400
WHOLE = {"steps", "flights_climbed", "stand_hours"}  # summed from fractional hourly values


def _shown(key: str, value: float) -> float:
    return round(value) if key in WHOLE else value


def _metric_days(conn, name: str, d_from: str, d_to: str) -> dict[str, dict]:
    m = samples.metric(conn, name)
    return samples.daily(conn, m, d_from, d_to) if m else {}


def _sleep(conn, d_from: str, d_to: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for key, name in SLEEP_METRICS.items():
        for d, v in _metric_days(conn, name, d_from, d_to).items():
            out.setdefault(d, {})[key] = round(v["value"])
    for v in out.values():
        asleep = sum(v.get(k, 0) for k, n in SLEEP_METRICS.items() if n in SLEEP_ASLEEP)
        if asleep:
            v["asleep_min"] = asleep
            v["asleep_hours"] = round(asleep / 60, 2)
    return out


def _sum_nutrients(meals: list[dict]) -> dict:
    totals: dict[str, float] = {}
    for m in meals:
        for k, v in (m["data"].get("totals") or {}).items():
            if k in NUTRIENTS:
                totals[k] = round(totals.get(k, 0) + v, 1)
    return totals


def day(conn, d: str) -> dict:
    try:
        date.fromisoformat(d)
    except ValueError:
        raise bad_request("date must be YYYY-MM-DD") from None
    prof = profile.get_all(conn)
    goals = prof.get("goals") or {}
    recs = records.search(conn, date=d, order="asc", limit=records.MAX_LIMIT)["records"]
    meals = [r for r in recs if r["category"] == "meal"]
    intake = _sum_nutrients(meals)
    activity = {k: _shown(k, v[d]["value"]) for k, name in DAY_METRICS.items()
                if d in (v := _metric_days(conn, name, d, d))}
    if "water_ml" in intake:
        activity["water_ml"] = round(activity.get("water_ml", 0) + intake["water_ml"])
    rings = next((r["data"] for r in recs if r["category"] == "activity_summary"), None)
    weight = samples.latest(conn, "body_mass", d)
    body = {"weight_kg": weight["value"], "weight_date": weight["local_date"]} if weight else {}
    burned = (activity.get("active_kcal") or 0) + (activity.get("basal_kcal") or 0)
    remaining = {k: round(goals[k] - intake.get(k, 0), 1) for k in NUTRIENTS
                 if isinstance(goals.get(k), (int, float))}
    return {
        "date": d,
        "timezone": prof.get("timezone"),
        "nutrition": {
            "intake": intake,
            "goals": {k: v for k, v in goals.items() if k in NUTRIENTS},
            "remaining": remaining,
            "meals": [{"id": m["id"], "start": m["start"], "meal_type": m["data"].get("meal_type"),
                       "title": m["title"], "kcal": (m["data"].get("totals") or {}).get("kcal")}
                      for m in meals],
        },
        "activity": activity,
        "rings": rings,
        "energy_balance": {
            "intake_kcal": intake.get("kcal", 0),
            "burned_kcal": round(burned) if burned else None,
            "net_kcal": round(intake.get("kcal", 0) - burned) if burned else None,
        },
        "sleep": _sleep(conn, d, d).get(d),
        "workouts": [{"id": r["id"], "start": r["start"], "end": r["end"], "title": r["title"],
                      **{k: r["data"].get(k) for k in ("activity_type", "duration_min",
                                                       "active_kcal", "distance_km")
                         if r["data"].get(k) is not None}}
                     for r in recs if r["category"] == "workout"],
        "body": body,
        "goals": goals,
        "other_records": [{"id": r["id"], "category": r["category"], "start": r["start"],
                           "title": r["title"]}
                          for r in recs if r["category"] not in
                          ("meal", "workout", "activity_summary")],
    }


def days(conn, d_from: str, d_to: str) -> dict:
    try:
        start, end = date.fromisoformat(d_from), date.fromisoformat(d_to)
    except ValueError:
        raise bad_request("from and to must be YYYY-MM-DD") from None
    if end < start or (end - start).days >= MAX_RANGE_DAYS:
        raise bad_request(f"need from <= to and at most {MAX_RANGE_DAYS} days")
    intake: dict[str, dict] = {}
    for r in conn.execute(
            "SELECT local_date, data FROM records WHERE category = 'meal'"
            " AND deleted_at IS NULL AND local_date BETWEEN ? AND ?", (d_from, d_to)):
        totals = loads(r["data"]).get("totals") or {}
        acc = intake.setdefault(r["local_date"], {"meals": 0})
        acc["meals"] += 1
        for k in ("kcal", "protein_g", "fat_g", "carbs_g"):
            if k in totals:
                acc[k] = round(acc.get(k, 0) + totals[k], 1)
    picked = {k: DAY_METRICS[k] for k in ("steps", "active_kcal", "exercise_min", "resting_hr",
                                          "water_ml")}
    metric_days = {k: _metric_days(conn, n, d_from, d_to) for k, n in picked.items()}
    weight = _metric_days(conn, "body_mass", d_from, d_to)
    sleep = _sleep(conn, d_from, d_to)
    rows = []
    for i in range((end - start).days + 1):
        d = (start + timedelta(days=i)).isoformat()
        row = {"date": d, "intake": intake.get(d, {})}
        row.update({k: _shown(k, v[d]["value"]) for k, v in metric_days.items() if d in v})
        if d in weight:
            row["weight_kg"] = weight[d]["value"]
        if sleep.get(d, {}).get("asleep_hours"):
            row["sleep_hours"] = sleep[d]["asleep_hours"]
        rows.append(row)
    return {"from": d_from, "to": d_to, "days": rows}
