"""Samples: numeric time series (steps, heart rate, weight, water, sleep stages ...).

Several devices often record the same thing (iPhone and Apple Watch both count steps), so
daily totals of `sum` metrics take, for each hour, the largest per-source total instead of
adding sources together. Sleep stages do the same per night. Apple Health data that arrived
twice, from the export file and from Health Auto Export, counts once per day (see `_hours`).
"""
from datetime import date, datetime, timedelta

from . import metrics as mx
from .db import dumps, loads, log_change, tx
from .errors import bad_request, not_found
from .timeutil import iso, parse, utc_now

MAX_BATCH = 5000
MAX_LIMIT = 1000
APPLE_COPIES = ("apple_health", "health_auto_export")  # source prefixes of the same Apple Health data
HOUR_KEY = "CAST(start_ts / 3600 AS INTEGER)"
INSERT_SQL = (
    "INSERT INTO samples(metric, start_at, end_at, start_ts, end_ts, local_date, value, unit,"
    " source, meta, created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"
    " ON CONFLICT DO NOTHING"
)


def metric(conn, name: str) -> dict | None:
    r = conn.execute("SELECT * FROM metrics WHERE name = ?", (name,)).fetchone()
    return dict(r) if r else None


def ensure_metric(conn, name: str, unit: str | None, *, aggregation: str | None = None,
                  title: str | None = None, cache: dict | None = None) -> dict:
    if cache is not None and name in cache:
        return cache[name]
    m = metric(conn, name)
    if m is None:
        if not name or len(name) > 64 or mx.snake(name) != name:
            raise bad_request(f"bad metric name {name!r}", "use snake_case, e.g. body_mass")
        conn.execute(
            "INSERT INTO metrics(name, title, unit, aggregation, date_by, created_at)"
            " VALUES (?, ?, ?, ?, 'start', ?)",
            (name, title or name, mx.unit_name(unit) if unit else "count",
             aggregation or mx.guess_aggregation(name),
             utc_now()))
        m = metric(conn, name)
    if cache is not None:
        cache[name] = m
    return m


def wake_day(end_iso: str) -> str:
    """The morning a stretch of sleep belongs to: sleep that ends after 18:00 counts toward the
    night ahead, so a night is one date even when it starts before midnight."""
    return (datetime.fromisoformat(end_iso) + timedelta(hours=6)).date().isoformat()


def row_for(m: dict, value: float, unit: str | None, start_iso: str, start_ts: float,
            start_date: str, end_iso: str, end_ts: float, end_date: str, source: str,
            meta: dict | None, actor: str, now: str) -> tuple:
    value, unit = mx.convert(m["name"], float(value), unit or m["unit"], m["unit"])
    return (m["name"], start_iso, end_iso, start_ts, end_ts,
            wake_day(end_iso) if m["date_by"] == "end" else start_date, round(value, 6), unit,
            source, dumps(meta) if meta else None, actor, now)


def add(conn, items: list[dict], actor: str, tz) -> dict:
    """API write path: validate each sample, insert, report ids, duplicates and warnings.

    A metric name agents use for a known metric (weight_kg, steps) is renamed, and a unit is
    converted into the metric's own. A unit that can't be converted is refused, since the value
    would be added up with values in the metric's unit."""
    if not isinstance(items, list) or not items:
        raise bad_request("send {\"samples\": [...]} with at least one sample")
    if len(items) > MAX_BATCH:
        raise bad_request(f"at most {MAX_BATCH} samples per request")
    now, cache, out, dupes, warnings, named = utc_now(), {}, [], 0, [], set()
    with tx(conn):
        for i, s in enumerate(items):
            if not isinstance(s, dict) or "metric" not in s or "value" not in s:
                raise bad_request(f"samples[{i}] needs metric and value")
            if not isinstance(s["value"], (int, float)) or isinstance(s["value"], bool):
                raise bad_request(f"samples[{i}].value must be a number")
            name, unit = mx.canonical(s["metric"]), s.get("unit")
            if s["metric"] not in named:
                named.add(s["metric"])
                if mx.snake(s["metric"]) != name:
                    warnings.append(f"metric {s['metric']} is {name}; send {name} next time")
                elif metric(conn, name) is None:
                    warnings.append(f"created metric {name}; if you meant an existing one, "
                                    "GET /metrics lists them")
            m = ensure_metric(conn, name, unit, cache=cache)
            if unit and mx.convert(name, 1.0, unit, m["unit"])[1] != m["unit"]:
                raise bad_request(f"samples[{i}]: {name} is in {m['unit']}, and {unit!r} cannot "
                                  "be converted to it",
                                  f"send the value in one of {', '.join(mx.units_into(m['unit']))}")
            try:
                start = parse(s.get("start"), tz)
                end = parse(s["end"], tz) if s.get("end") else start
            except ValueError as e:
                raise bad_request(f"samples[{i}]: {e}") from None
            if end < start:
                raise bad_request(f"samples[{i}]: end is before start")
            row = row_for(m, s["value"], s.get("unit"), iso(start), start.timestamp(),
                          start.date().isoformat(), iso(end), end.timestamp(),
                          end.date().isoformat(), s.get("source") or "agent", s.get("meta"),
                          actor, now)
            cur = conn.execute(INSERT_SQL + " RETURNING id", row)
            got = cur.fetchone()
            if got is None:
                dupes += 1
                continue
            sample = get(conn, got[0])
            out.append(sample)
            log_change(conn, actor, "create", "sample", str(got[0]), after=sample, at=now)
    return {"inserted": len(out), "duplicates": dupes, "samples": out, "warnings": warnings}


def _row(r) -> dict:
    return {"id": r["id"], "metric": r["metric"], "start": r["start_at"], "end": r["end_at"],
            "local_date": r["local_date"], "value": r["value"], "unit": r["unit"],
            "source": r["source"], "meta": loads(r["meta"]), "created_by": r["created_by"],
            "created_at": r["created_at"]}


def get(conn, sample_id: int) -> dict:
    r = conn.execute("SELECT * FROM samples WHERE id = ?", (sample_id,)).fetchone()
    if r is None:
        raise not_found("sample", str(sample_id))
    return _row(r)


def delete(conn, sample_id: int, actor: str) -> dict:
    with tx(conn):
        before = get(conn, sample_id)
        conn.execute("DELETE FROM samples WHERE id = ?", (sample_id,))
        log_change(conn, actor, "delete", "sample", str(sample_id), before=before)
    return before


def search(conn, *, metric_name=None, date_from=None, date_to=None, source=None, limit=100,
           cursor=None) -> dict:
    where, args = [], []
    if metric_name:
        where.append("metric = ?")
        args.append(mx.canonical(metric_name))
    if date_from:
        where.append("local_date >= ?")
        args.append(date_from)
    if date_to:
        where.append("local_date <= ?")
        args.append(date_to)
    if source:
        where.append("source = ?")
        args.append(source)
    if cursor:
        if not str(cursor).isdigit():
            raise bad_request("bad cursor; pass next_cursor from the previous page")
        where.append("id < ?")
        args.append(int(cursor))
    limit = max(1, min(int(limit), MAX_LIMIT))
    rows = conn.execute(
        "SELECT * FROM samples" + (" WHERE " + " AND ".join(where) if where else "")
        + " ORDER BY id DESC LIMIT ?", [*args, limit + 1]).fetchall()
    return {"samples": [_row(r) for r in rows[:limit]],
            "next_cursor": str(rows[limit - 1]["id"]) if len(rows) > limit else None}


def _dedupe_key(name: str) -> str:
    # Sleep: one source wins per night. Everything else: one source wins per hour.
    return "0" if name.startswith("sleep_") else HOUR_KEY


def _hours(conn, name: str, d_from: str, d_to: str, key: str) -> dict[str, tuple[dict, int]]:
    """{local_date: ({bucket: {source: value}}, samples)} for a `sum` metric. One source counts
    per bucket, the larger (iPhone and Watch both counting a walk).

    The export file and Health Auto Export carry the same Apple Health data but bucket it
    differently, so each day keeps the copy with the larger total. The other adds only buckets
    whose time no kept sample covers, and where it runs on past either end of the kept copy it
    may also stand in for that copy's short edge bucket. Health Auto Export's hourly points are
    instants at the top of the hour; each stands for the hour after it. Other sources, such as an
    agent or the web page, count per bucket against all of that as before."""
    days: dict[str, dict] = {}
    for r in conn.execute(f"""
            SELECT local_date, {key} AS h, source, SUM(value) AS v, COUNT(*) AS n,
                   MIN(start_ts) AS s,
                   MAX(CASE WHEN end_ts > start_ts THEN end_ts ELSE start_ts + 3600 END) AS e
            FROM samples WHERE metric = ? AND local_date BETWEEN ? AND ?
            GROUP BY local_date, h, source""", (name, d_from, d_to)):
        day = days.setdefault(r["local_date"], {"n": 0, "copies": {}, "other": {}})
        day["n"] += r["n"]
        copy = r["source"].split(":", 1)[0]
        if copy not in APPLE_COPIES:
            day["other"].setdefault(r["h"], {})[r["source"]] = r["v"]
            continue
        b = day["copies"].setdefault(copy, {}).setdefault(r["h"], {"src": {}, "s": r["s"], "e": r["e"]})
        b["src"][r["source"]] = r["v"]
        b["s"], b["e"] = min(b["s"], r["s"]), max(b["e"], r["e"])
    out = {}
    for d, day in days.items():
        kept: dict = {}
        copies = sorted(day["copies"].items(),
                        key=lambda c: (-sum(max(b["src"].values()) for b in c[1].values()), c[0]))
        if copies:
            first = copies[0][1]
            kept = {h: dict(b["src"]) for h, b in first.items()}
            covered = [(b["s"], b["e"]) for b in first.values()]
            lo, hi, first_h, last_h = (min(s for s, _ in covered), max(e for _, e in covered),
                                       min(first), max(first))
            for _, buckets in copies[1:]:
                later = any(b["s"] >= hi for b in buckets.values())
                earlier = any(b["e"] <= lo for b in buckets.values())
                for h, b in sorted(buckets.items()):
                    if h in kept:
                        if (h == last_h and later) or (h == first_h and earlier):
                            kept[h].update(b["src"])  # the kept copy stops or starts within it
                    elif not any(b["s"] < e and s < b["e"] for s, e in covered):
                        kept[h] = dict(b["src"])
                        covered.append((b["s"], b["e"]))
        for h, src in day["other"].items():
            kept.setdefault(h, {}).update(src)
        out[d] = (kept, day["n"])
    return out


def daily(conn, m: dict, d_from: str, d_to: str) -> dict[str, dict]:
    """{local_date: {value, count, min?, max?}} for one metric over an inclusive date range."""
    name, args = m["name"], (m["name"], d_from, d_to)
    if m["aggregation"] == "sum":
        return {d: {"value": round(sum(max(src.values()) for src in hours.values()), 2),
                    "count": n}
                for d, (hours, n) in _hours(conn, name, d_from, d_to, _dedupe_key(name)).items()}
    if m["aggregation"] == "last":
        rows = conn.execute("""
            SELECT local_date, value, n FROM (
              SELECT local_date, value, COUNT(*) OVER w AS n,
                     ROW_NUMBER() OVER (PARTITION BY local_date ORDER BY start_ts DESC) AS rn
              FROM samples WHERE metric = ? AND local_date BETWEEN ? AND ?
              WINDOW w AS (PARTITION BY local_date))
            WHERE rn = 1""", args)
        return {r["local_date"]: {"value": round(r["value"], 2), "count": r["n"]} for r in rows}
    rows = conn.execute("""
        SELECT local_date, AVG(value) AS value, MIN(value) AS lo, MAX(value) AS hi,
               COUNT(*) AS count
        FROM samples WHERE metric = ? AND local_date BETWEEN ? AND ?
        GROUP BY local_date""", args)
    return {r["local_date"]: {"value": round(r["value"], 2), "min": r["lo"], "max": r["hi"],
                              "count": r["count"]} for r in rows}


def _bucket_label(d: str, bucket: str) -> str:
    if bucket == "week":
        day = date.fromisoformat(d)
        return (day - timedelta(days=day.weekday())).isoformat()
    return d[:7] if bucket == "month" else d


def series(conn, name: str, d_from: str, d_to: str, bucket: str, tz) -> dict:
    m = metric(conn, mx.canonical(name))
    if m is None:
        raise not_found("metric", name, "GET /api/v1/metrics lists metrics that have data")
    if bucket == "hour":
        if (date.fromisoformat(d_to) - date.fromisoformat(d_from)).days > 31:
            raise bad_request("hour buckets cover at most 31 days")
        if m["aggregation"] == "sum":
            by_hour: dict[int, dict] = {}  # each source added up across dates, then the larger
            for hours, _ in _hours(conn, m["name"], d_from, d_to, HOUR_KEY).values():
                for h, src in hours.items():
                    acc = by_hour.setdefault(h, {})
                    for s, v in src.items():
                        acc[s] = acc.get(s, 0) + v
            rows = [{"h": h, "value": max(src.values())} for h, src in sorted(by_hour.items())]
        else:
            agg = "AVG(v)" if m["aggregation"] == "avg" else "MAX(v)"
            rows = conn.execute(f"""
                SELECT h, {agg} AS value FROM (
                  SELECT {HOUR_KEY} AS h, source, AVG(value) AS v
                  FROM samples WHERE metric = ? AND local_date BETWEEN ? AND ?
                  GROUP BY h, source)
                GROUP BY h ORDER BY h""", (m["name"], d_from, d_to))
        points = [{"bucket": iso(datetime.fromtimestamp(r["h"] * 3600, tz)),
                   "value": round(r["value"], 2)} for r in rows]
    else:
        points, acc = [], {}
        for d, v in sorted(daily(conn, m, d_from, d_to).items()):
            key = _bucket_label(d, bucket)
            a = acc.setdefault(key, {"bucket": key, "days": 0, "sum": 0.0, "count": 0,
                                     "min": None, "max": None, "last": None})
            a["days"] += 1
            a["sum"] += v["value"]
            a["count"] += v["count"]
            a["last"] = v["value"]
            for k, f in (("min", min), ("max", max)):
                x = v.get(k, v["value"])
                a[k] = x if a[k] is None else f(a[k], x)
        for a in acc.values():
            value = {"sum": a["sum"], "avg": a["sum"] / a["days"],
                     "last": a["last"]}[m["aggregation"]]
            p = {"bucket": a["bucket"], "value": round(value, 2), "days_with_data": a["days"],
                 "samples": a["count"]}
            if m["aggregation"] == "avg":
                p.update(min=a["min"], max=a["max"])
            points.append(p)
    return {"metric": m["name"], "title": m["title"], "unit": m["unit"],
            "aggregation": m["aggregation"], "bucket": bucket, "from": d_from, "to": d_to,
            "points": points}


def catalog(conn, with_counts: bool = False) -> list[dict]:
    """Every metric with the date range it has data for (index seeks, cheap at any size)."""
    out = []
    for r in conn.execute("SELECT * FROM metrics ORDER BY name"):
        first = conn.execute("SELECT MIN(local_date) FROM samples WHERE metric = ?",
                             (r["name"],)).fetchone()[0]
        last = conn.execute("SELECT MAX(local_date) FROM samples WHERE metric = ?",
                            (r["name"],)).fetchone()[0]
        item = {"name": r["name"], "title": r["title"], "unit": r["unit"],
                "aggregation": r["aggregation"], "date_by": r["date_by"],
                "first_date": first, "last_date": last}
        if with_counts:
            item["samples"] = conn.execute("SELECT COUNT(*) FROM samples WHERE metric = ?",
                                           (r["name"],)).fetchone()[0]
        out.append(item)
    out.sort(key=lambda m: (m["last_date"] is None, m["name"]))
    return out


def latest(conn, name: str, on_or_before: str) -> dict | None:
    r = conn.execute("SELECT * FROM samples WHERE metric = ? AND local_date <= ?"
                     " ORDER BY start_ts DESC LIMIT 1", (name, on_or_before)).fetchone()
    return _row(r) if r else None
