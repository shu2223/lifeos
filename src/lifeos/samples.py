"""Samples: numeric time series (steps, heart rate, weight, water, sleep stages ...).

Several devices often record the same thing (iPhone and Apple Watch both count steps), so
daily totals of `sum` metrics take, for each hour, the largest per-source total instead of
adding sources together. Sleep stages do the same per night.
"""
from datetime import date, datetime, timedelta

from . import metrics as mx
from .db import dumps, loads, log_change, tx
from .errors import bad_request, not_found
from .timeutil import iso, parse, utc_now

MAX_BATCH = 5000
MAX_LIMIT = 1000
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
            (name, title or name, unit or "count", aggregation or mx.guess_aggregation(name),
             utc_now()))
        m = metric(conn, name)
    if cache is not None:
        cache[name] = m
    return m


def row_for(m: dict, value: float, unit: str | None, start_iso: str, start_ts: float,
            start_date: str, end_iso: str, end_ts: float, end_date: str, source: str,
            meta: dict | None, actor: str, now: str) -> tuple:
    value, unit = mx.convert(m["name"], float(value), unit or m["unit"], m["unit"])
    return (m["name"], start_iso, end_iso, start_ts, end_ts,
            end_date if m["date_by"] == "end" else start_date, round(value, 6), unit, source,
            dumps(meta) if meta else None, actor, now)


def add(conn, items: list[dict], actor: str, tz) -> dict:
    """API write path: validate each sample, insert, report ids and duplicates."""
    if not isinstance(items, list) or not items:
        raise bad_request("send {\"samples\": [...]} with at least one sample")
    if len(items) > MAX_BATCH:
        raise bad_request(f"at most {MAX_BATCH} samples per request")
    now, cache, out, dupes = utc_now(), {}, [], 0
    with tx(conn):
        for i, s in enumerate(items):
            if not isinstance(s, dict) or "metric" not in s or "value" not in s:
                raise bad_request(f"samples[{i}] needs metric and value")
            if not isinstance(s["value"], (int, float)) or isinstance(s["value"], bool):
                raise bad_request(f"samples[{i}].value must be a number")
            m = ensure_metric(conn, mx.canonical(s["metric"]), s.get("unit"), cache=cache)
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
    return {"inserted": len(out), "duplicates": dupes, "samples": out}


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
    return "0" if name.startswith("sleep_") else "CAST(start_ts / 3600 AS INTEGER)"


def daily(conn, m: dict, d_from: str, d_to: str) -> dict[str, dict]:
    """{local_date: {value, count, min?, max?}} for one metric over an inclusive date range."""
    name, args = m["name"], (m["name"], d_from, d_to)
    if m["aggregation"] == "sum":
        rows = conn.execute(f"""
            SELECT local_date, SUM(v) AS value, SUM(n) AS count FROM (
              SELECT local_date, h, MAX(v) AS v, SUM(n) AS n FROM (
                SELECT local_date, {_dedupe_key(name)} AS h, source, SUM(value) AS v,
                       COUNT(*) AS n
                FROM samples WHERE metric = ? AND local_date BETWEEN ? AND ?
                GROUP BY local_date, h, source)
              GROUP BY local_date, h)
            GROUP BY local_date""", args)
        return {r["local_date"]: {"value": round(r["value"], 2), "count": r["count"]}
                for r in rows}
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
        agg = {"sum": "MAX(v)", "avg": "AVG(v)", "last": "MAX(v)"}[m["aggregation"]]
        inner = "SUM(value)" if m["aggregation"] == "sum" else "AVG(value)"
        rows = conn.execute(f"""
            SELECT h, {agg} AS value FROM (
              SELECT CAST(start_ts / 3600 AS INTEGER) AS h, source, {inner} AS v
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
