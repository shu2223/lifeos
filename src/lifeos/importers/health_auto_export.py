"""Health Auto Export (iOS app) REST API automation payloads.

Format: {"data": {"metrics": [...], "workouts": [...], ...}}; most dates look like
"2024-02-06 14:30:00 -0800". The raw payload is always kept on disk by the caller, so arrays we
do not parse yet (stateOfMind, ecg, ...) are not lost.
"""
from .. import metrics as mx
from .. import profile, records
from ..samples import INSERT_SQL, ensure_metric, row_for
from ..timeutil import apple, utc_now
from . import Batch, activity_type, gpx, has_attachment, workout_key

SOURCE = "health_auto_export"
SLEEP_STAGES = {"Awake": "sleep_awake", "Asleep": "sleep_asleep", "In Bed": "sleep_in_bed",
                "Core": "sleep_core", "REM": "sleep_rem", "Deep": "sleep_deep",
                "Unspecified": "sleep_asleep"}
SLEEP_SUMMARY = {"asleep": "sleep_asleep", "core": "sleep_core", "deep": "sleep_deep",
                 "rem": "sleep_rem", "awake": "sleep_awake", "inBed": "sleep_in_bed"}
POINT_KEYS = {"date", "qty", "source", "start", "end", "startDate", "endDate", "units"}


def _date(s: str | None):
    if not s:
        return None
    try:
        return apple(s)
    except (ValueError, IndexError):
        return None


def _qty(obj, target: str) -> float | None:
    if isinstance(obj, dict) and isinstance(obj.get("qty"), (int, float)):
        return round(mx.convert("", obj["qty"], obj.get("units") or target, target)[0], 2)
    return None


class _Importer:
    def __init__(self, conn, actor: str, files_dir):
        self.conn, self.actor, self.files_dir = conn, actor, files_dir
        self.tz = profile.timezone_of(conn)
        self.now, self.cache, self.rows = utc_now(), {}, []
        self.stats = {"samples_read": 0, "samples_added": 0, "workouts": 0, "routes": 0,
                      "unparsed_points": 0, "raw_only": {}}

    def add(self, name: str, value, unit, start, end=None, src="", meta=None) -> None:
        if not isinstance(value, (int, float)) or start is None:
            self.stats["unparsed_points"] += 1
            return
        m = ensure_metric(self.conn, name, unit, cache=self.cache)
        end = end or start
        self.rows.append(row_for(m, value, unit, *start, *end, f"{SOURCE}:{src or 'unknown'}",
                                 meta, self.actor, self.now))
        self.stats["samples_read"] += 1

    def metric(self, m: dict) -> None:
        name, units = mx.canonical(m.get("name", "")), m.get("units")
        for p in m.get("data") or []:
            start = _date(p.get("start") or p.get("startDate") or p.get("date"))
            end = _date(p.get("end") or p.get("endDate"))
            src = p.get("source", "")
            if name == "sleep_analysis":
                if "qty" in p:  # one segment per point
                    stage = SLEEP_STAGES.get(p.get("value", ""), "sleep_asleep")
                    self.add(stage, p["qty"] * 60, "min", start, end, src)
                    continue
                for key, stage in SLEEP_SUMMARY.items():  # one night per point
                    if not p.get(key):
                        continue
                    s_key, e_key = ("inBedStart", "inBedEnd") if key == "inBed" else (
                        "sleepStart", "sleepEnd")
                    self.add(stage, p[key] * 60, "min", _date(p.get(s_key)) or start,
                             _date(p.get(e_key)) or end, src)
            elif name == "blood_pressure":
                self.add("blood_pressure_systolic", p.get("systolic"), units, start, end, src)
                self.add("blood_pressure_diastolic", p.get("diastolic"), units, start, end, src)
            elif "qty" in p:
                meta = {k: v for k, v in p.items() if k not in POINT_KEYS} or None
                self.add(name, p["qty"], units, start, end, src, meta)
            elif "Avg" in p:
                self.add(name, p["Avg"], units, start, end, src,
                         {"min": p.get("Min"), "max": p.get("Max")})
            else:
                self.stats["unparsed_points"] += 1

    def workout(self, w: dict) -> None:
        start, end = _date(w.get("start")), _date(w.get("end"))
        if start is None:
            self.stats["unparsed_points"] += 1
            return
        name = w.get("name") or "Workout"
        data: dict = {"activity_type": activity_type(name) or "other", "activity_name": name,
                      "import_via": "health_auto_export"}
        if isinstance(w.get("duration"), (int, float)):
            data["duration_min"] = round(w["duration"] / 60, 2)
        for key, target, sources in (
                ("active_kcal", "kcal", ("activeEnergyBurned", "activeEnergy")),
                ("total_kcal", "kcal", ("totalEnergy",)),
                ("distance_km", "km", ("distance",)),
                ("elevation_up_m", "m", ("elevationUp",)),
                ("avg_hr", "count/min", ("avgHeartRate",)),
                ("max_hr", "count/min", ("maxHeartRate",))):
            for src in sources:
                v = _qty(w.get(src), target)
                if v is not None:
                    data[key] = v
                    break
        hr = w.get("heartRate") or {}
        for k, key in (("avg", "avg_hr"), ("max", "max_hr"), ("min", "min_hr")):
            v = _qty(hr.get(k), "count/min")
            if v is not None:
                data.setdefault(key, v)
        steps = w.get("stepCount")
        if isinstance(steps, list):
            data["step_count"] = round(sum(s.get("qty", 0) for s in steps if isinstance(s, dict)))
        if isinstance(w.get("isIndoor"), bool):
            data["indoor"] = w["isIndoor"]
        if w.get("location"):
            data["location"] = w["location"]
        if isinstance(w.get("source"), dict) and w["source"].get("name"):
            data["device_source"] = w["source"]["name"]
        if w.get("id"):
            data["hae_id"] = w["id"]
        rec, _, _ = records.upsert(self.conn, {
            "category": "workout", "start": start[0], "end": end[0] if end else None,
            "local_date": start[2], "data": data, "source": "apple_health",
            "external_id": workout_key(start[1])}, self.actor, self.tz, log=False)
        self.stats["workouts"] += 1
        route = w.get("route")
        if isinstance(route, list) and route:
            filename = f"route-{start[0][:16].replace(':', '')}.gpx"
            if not has_attachment(self.conn, rec["id"], filename):
                pts = [(p.get("latitude", p.get("lat")), p.get("longitude", p.get("lon")),
                        p.get("altitude"), (_date(p.get("timestamp")) or (None,))[0])
                       for p in route if isinstance(p, dict)]
                pts = [p for p in pts if p[0] is not None and p[1] is not None]
                if pts:
                    records.add_attachment(self.conn, self.files_dir, rec["id"], filename,
                                           "application/gpx+xml", gpx(name, pts), self.actor,
                                           log=False)
                    self.stats["routes"] += 1

    def run(self, payload: dict) -> dict:
        data = payload.get("data", payload) if isinstance(payload, dict) else {}
        if not isinstance(data, dict):
            raise ValueError('expected {"data": {"metrics": [...], "workouts": [...]}}')
        with Batch(self.conn, size=10**9):
            for m in data.get("metrics") or []:
                if isinstance(m, dict):
                    self.metric(m)
            for w in data.get("workouts") or []:
                if isinstance(w, dict):
                    self.workout(w)
            if self.rows:
                before = self.conn.total_changes
                self.conn.executemany(INSERT_SQL, self.rows)
                self.stats["samples_added"] = self.conn.total_changes - before
        for key, value in data.items():
            if key not in ("metrics", "workouts") and isinstance(value, list) and value:
                self.stats["raw_only"][key] = len(value)
        return self.stats


def run(conn, payload: dict, actor: str, files_dir) -> dict:
    return _Importer(conn, actor, files_dir).run(payload)
