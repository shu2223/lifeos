"""Health Auto Export (iOS app) REST API automation payloads.

Format: {"data": {"metrics": [...], "workouts": [...], ...}}; most dates look like
"2024-02-06 14:30:00 -0800". The raw payload is always kept on disk by the caller, so arrays we
do not parse yet (stateOfMind, ecg, ...) are not lost.
"""
from .. import metrics as mx
from .. import profile, records, series
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
# Workout fields sent as {qty, units}: the data key and the unit stored (None keeps the number).
WORKOUT_SCALARS = {
    "activeEnergyBurned": ("active_kcal", "kcal"), "activeEnergy": ("active_kcal", "kcal"),
    "totalEnergy": ("total_kcal", "kcal"), "distance": ("distance_km", "km"),
    "elevationUp": ("elevation_up_m", "m"), "elevationDown": ("elevation_down_m", "m"),
    "avgHeartRate": ("avg_hr", "count/min"), "maxHeartRate": ("max_hr", "count/min"),
    "speed": ("speed_kmh", "km/hr"), "avgSpeed": ("avg_speed_kmh", "km/hr"),
    "maxSpeed": ("max_speed_kmh", "km/hr"), "temperature": ("temperature_c", "degC"),
    "humidity": ("humidity_pct", "%"), "stepCadence": ("step_cadence", "count/min"),
    "flightsClimbed": ("flights_climbed", "count"), "swimCadence": ("swim_cadence", "count/min"),
    "totalSwimmingStrokeCount": ("swim_strokes", "count"), "lapLength": ("lap_length_m", "m"),
    "intensity": ("intensity", None), "effortScore": ("effort_score", None),
    "estimatedEffortScore": ("estimated_effort_score", None),
}
# Arrays sent with "Include Workout Metrics": stored per workout, never as day samples,
# because the Health Metrics automation already sends the same readings.
WORKOUT_SERIES = {
    "heartRateData": ("heart_rate", "count/min"),
    "heartRateRecovery": ("heart_rate_recovery", "count/min"),
    "activeEnergy": ("active_energy", "kcal"), "basalEnergy": ("basal_energy", "kcal"),
    "walkingAndRunningDistance": ("walking_running_distance", "km"),
    "cyclingDistance": ("cycling_distance", "km"), "cyclingSpeed": ("cycling_speed", "km/hr"),
    "cyclingCadence": ("cycling_cadence", "count/min"), "cyclingPower": ("cycling_power", "W"),
    "swimDistance": ("swimming_distance", "m"), "swimStroke": ("swim_stroke", "count"),
    "stepCount": ("step_count", "count"),
}
WORKOUT_HANDLED = {"id", "name", "start", "end", "duration", "source", "location", "isIndoor",
                   "route", "metadata", "heartRate"}
# The app re-sends recent days on every run, and an hour or a night that was still in progress
# last time comes back with a bigger number. A point is identified by (metric, source, start):
# the newer value replaces the older one instead of being added to it.
REPLACE_SQL = ("DELETE FROM samples WHERE metric = ? AND source = ? AND start_ts = ?"
               " AND NOT (end_ts = ? AND value = ?)")


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
        self.stats = {"samples_read": 0, "samples_added": 0, "workouts": 0, "series": 0,
                      "routes": 0, "unparsed_points": 0, "raw_only": {}}

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

    def _series(self, key: str, items: list):
        """(name, unit, kind, [(ts, point)]) for an array the workout carries."""
        name, target = WORKOUT_SERIES.get(key, (mx.snake(key), None))
        pts = [p for p in items if isinstance(p, dict)]
        if not pts or not all("date" in p and ("qty" in p or "Avg" in p) for p in pts):
            return name, "", "raw", [(None, p) for p in items]
        ranged = any("Avg" in p for p in pts)
        given = pts[0].get("units") or ""
        # Convert to our unit only when we know how; otherwise keep the sender's unit.
        unit = target if target and mx.convert(name, 1.0, given or target, target)[1] == target \
            else given

        def conv(x, src):
            if not isinstance(x, (int, float)) or isinstance(x, bool):
                return None
            return round(mx.convert(name, x, src or unit, unit)[0], 3)
        out = []
        for p in pts:
            t = _date(p.get("date"))
            if t is None:
                continue
            src = p.get("units") or given
            vals = [conv(p.get(k), src) for k in (("Min", "Avg", "Max") if ranged else ("qty",))]
            out.append((t[1], [t[0], *vals]))
        out.sort(key=lambda x: x[0])
        return name, unit, "range" if ranged else "value", out

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
        other: dict = {}
        curves: dict[str, tuple] = {}
        for key, value in w.items():
            if key in WORKOUT_HANDLED or value in (None, "", [], {}):
                continue
            if isinstance(value, list):
                s = self._series(key, value)
                curves[s[0]] = s
            elif isinstance(value, dict) and isinstance(value.get("qty"), (int, float)):
                field, target = WORKOUT_SCALARS.get(key, (None, None))
                qty, unit = value["qty"], value.get("units") or ""
                if field and target:
                    v, got = mx.convert("", qty, unit or target, target)
                    if got == target:
                        data.setdefault(field, round(v, 2))
                        continue
                elif field:
                    data.setdefault(field, round(qty, 2))
                    continue
                other[mx.snake(key)] = {"value": qty, "unit": unit}
            else:
                other[mx.snake(key)] = value
        hr = w.get("heartRate") or {}
        for k, key in (("avg", "avg_hr"), ("max", "max_hr"), ("min", "min_hr")):
            v = _qty(hr.get(k), "count/min")
            if v is not None:
                data.setdefault(key, v)
        self._derive(data, curves, end)
        if isinstance(w.get("isIndoor"), bool):
            data["indoor"] = w["isIndoor"]
        if w.get("location"):
            data["location"] = w["location"]
        if isinstance(w.get("source"), dict) and w["source"].get("name"):
            data["device_source"] = w["source"]["name"]
        if isinstance(w.get("metadata"), dict) and w["metadata"]:
            data["metadata"] = w["metadata"]
        if other:
            data["other_stats"] = other
        if w.get("id"):
            data["hae_id"] = w["id"]
        rec, _, _ = records.upsert(self.conn, {
            "category": "workout", "start": start[0], "end": end[0] if end else None,
            "local_date": start[2], "data": data, "source": "apple_health",
            "external_id": workout_key(start[1])}, self.actor, self.tz, log=False)
        for s_name, unit, kind, pts in curves.values():
            series.upsert(self.conn, rec["id"], s_name, unit, kind, [p for _, p in pts], SOURCE)
            self.stats["series"] += 1
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

    @staticmethod
    def _derive(data: dict, curves: dict, end) -> None:
        """Headline numbers from the curves: heart-rate range, recovery, steps."""
        hr = curves.get("heart_rate")
        if hr and hr[2] == "range" and hr[3]:
            stats = series.stats_of("range", [p for _, p in hr[3]])
            data.setdefault("avg_hr", stats["avg"])
            data.setdefault("max_hr", stats["max"])
            data.setdefault("min_hr", stats["min"])
        rec = curves.get("heart_rate_recovery")
        if rec and rec[2] == "range" and rec[3] and end is not None:
            base = (hr[3][-1][1][2] if hr and hr[2] == "range" and hr[3] else rec[3][0][1][2])
            for secs, key in ((60, "hr_recovery_1min"), (120, "hr_recovery_2min")):
                ts, point = min(rec[3], key=lambda x: abs(x[0] - (end[1] + secs)))
                if abs(ts - (end[1] + secs)) <= 30 and base is not None and point[2] is not None:
                    data[key] = round(base - point[2], 1)
        steps = curves.get("step_count")
        if steps and steps[2] == "value" and steps[3]:
            data.setdefault("step_count", round(sum(p[1] or 0 for _, p in steps[3])))

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
                self.conn.executemany(REPLACE_SQL, [(r[0], r[8], r[3], r[4], r[6])
                                                    for r in self.rows])
                replaced = self.conn.total_changes - before
                before = self.conn.total_changes
                self.conn.executemany(INSERT_SQL, self.rows)
                self.stats["samples_added"] = self.conn.total_changes - before - replaced
                self.stats["samples_updated"] = replaced
        for key, value in data.items():
            if key not in ("metrics", "workouts") and isinstance(value, list) and value:
                self.stats["raw_only"][key] = len(value)
        return self.stats


def run(conn, payload: dict, actor: str, files_dir) -> dict:
    return _Importer(conn, actor, files_dir).run(payload)
