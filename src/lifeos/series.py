"""Time series that belong to one record: heart rate during and after a workout, cadence,
power, speed, distance per minute ...

They live apart from samples on purpose. The same heart beats and steps also arrive as
day-level samples, so storing them twice there would double daily totals.

Point layouts by kind:
  value  [[time, value], ...]
  range  [[time, min, avg, max], ...]
  raw    the list as the source sent it (laps, segments, events ...)
"""
from datetime import datetime

from .db import dumps, loads
from .timeutil import iso, utc_now


def _num(xs) -> list[float]:
    return [x for x in xs if isinstance(x, (int, float))]


def stats_of(kind: str, points: list) -> dict:
    if kind == "raw" or not points:
        return {"count": len(points)}
    if kind == "range":
        lows, avgs, highs = (_num(p[i] for p in points) for i in (1, 2, 3))
        vals = avgs
        out = {"min": min(lows or avgs), "max": max(highs or avgs)}
    else:
        vals = _num(p[1] for p in points)
        out = {"min": min(vals), "max": max(vals), "sum": round(sum(vals), 2)}
    out.update(count=len(points), avg=round(sum(vals) / len(vals), 2),
               first_at=points[0][0], last_at=points[-1][0])
    return out


def upsert(conn, record_id: str, name: str, unit: str, kind: str, points: list,
           source: str) -> dict:
    """Store or replace one series; a re-sent workout brings the complete curve."""
    stats = stats_of(kind, points)
    conn.execute(
        "INSERT INTO record_series(record_id, name, unit, kind, points, stats, source,"
        " updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
        " ON CONFLICT(record_id, name) DO UPDATE SET unit = excluded.unit,"
        " kind = excluded.kind, points = excluded.points, stats = excluded.stats,"
        " source = excluded.source, updated_at = excluded.updated_at",
        (record_id, name, unit, kind, dumps(points), dumps(stats), source, utc_now()))
    return stats


def listing(conn, record_id: str) -> list[dict]:
    """Names and stats only, for embedding in a record."""
    rows = conn.execute("SELECT name, unit, kind, stats FROM record_series WHERE record_id = ?"
                        " ORDER BY name", (record_id,))
    return [{"name": r["name"], "unit": r["unit"], "kind": r["kind"],
             "stats": loads(r["stats"])} for r in rows]


def _from_samples(conn, rec: dict, tz) -> dict | None:
    """Workouts from the Apple Health export have no curve of their own; the watch's heart
    rate readings inside the workout window are the same data."""
    if rec["category"] != "workout" or rec.get("end") is None:
        return None
    window = conn.execute("SELECT start_ts, end_ts FROM records WHERE id = ?",
                          (rec["id"],)).fetchone()
    rows = conn.execute(
        "SELECT start_ts, value FROM samples WHERE metric = 'heart_rate'"
        " AND start_ts >= ? AND start_ts <= ? ORDER BY start_ts",
        (window["start_ts"], window["end_ts"])).fetchall()
    if not rows:
        return None
    points = [[iso(datetime.fromtimestamp(r["start_ts"], tz)), r["value"]] for r in rows]
    return {"name": "heart_rate", "unit": "count/min", "kind": "value",
            "stats": stats_of("value", points), "source": "samples", "points": points}


def for_record(conn, rec: dict, tz, *, name: str | None = None,
               with_points: bool = True) -> list[dict]:
    sql = "SELECT * FROM record_series WHERE record_id = ?"
    args: list = [rec["id"]]
    if name:
        sql += " AND name = ?"
        args.append(name)
    out = []
    for r in conn.execute(sql + " ORDER BY name", args):
        item = {"name": r["name"], "unit": r["unit"], "kind": r["kind"],
                "stats": loads(r["stats"]), "source": r["source"]}
        if with_points:
            item["points"] = loads(r["points"])
        out.append(item)
    if name in (None, "heart_rate") and not any(s["name"] == "heart_rate" for s in out):
        derived = _from_samples(conn, rec, tz)
        if derived:
            if not with_points:
                derived.pop("points")
            out.insert(0, derived)
    return out
