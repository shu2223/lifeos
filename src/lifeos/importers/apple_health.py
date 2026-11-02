"""Apple Health full export: Health app → profile picture → Export All Health Data.

Reads export.zip (or the bare XML) as a stream, so multi-gigabyte exports fit in little memory.
Quantity and category samples become samples, workouts and Activity rings become records,
workout routes become GPX attachments. Re-importing a newer export only adds what is new.
"""
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from .. import metrics as mx
from .. import profile, records
from ..samples import INSERT_SQL, ensure_metric, row_for
from ..timeutil import apple, utc_now
from . import Batch, activity_type, has_attachment, workout_key

SOURCE = "apple_health"
SLEEP = {
    "HKCategoryValueSleepAnalysisInBed": "sleep_in_bed",
    "HKCategoryValueSleepAnalysisAsleep": "sleep_asleep",
    "HKCategoryValueSleepAnalysisAsleepUnspecified": "sleep_asleep",
    "HKCategoryValueSleepAnalysisAsleepCore": "sleep_core",
    "HKCategoryValueSleepAnalysisAsleepDeep": "sleep_deep",
    "HKCategoryValueSleepAnalysisAsleepREM": "sleep_rem",
    "HKCategoryValueSleepAnalysisAwake": "sleep_awake",
}
WORKOUT_STATS = {
    "ActiveEnergyBurned": ("sum", "active_kcal", "kcal"),
    "BasalEnergyBurned": ("sum", "basal_kcal", "kcal"),
    "DistanceWalkingRunning": ("sum", "distance_km", "km"),
    "DistanceCycling": ("sum", "distance_km", "km"),
    "DistanceSwimming": ("sum", "distance_km", "km"),
    "DistanceDownhillSnowSports": ("sum", "distance_km", "km"),
    "StepCount": ("sum", "step_count", "count"),
    "FlightsClimbed": ("sum", "flights_climbed", "count"),
}
SEX = {"HKBiologicalSexFemale": "female", "HKBiologicalSexMale": "male",
       "HKBiologicalSexOther": "other"}
BLOOD = {"HKBloodTypeAPositive": "A+", "HKBloodTypeANegative": "A-", "HKBloodTypeBPositive": "B+",
         "HKBloodTypeBNegative": "B-", "HKBloodTypeABPositive": "AB+",
         "HKBloodTypeABNegative": "AB-", "HKBloodTypeOPositive": "O+",
         "HKBloodTypeONegative": "O-"}


def _open(path: Path):
    """(xml stream, zipfile or None, directory of the xml inside the zip)."""
    if zipfile.is_zipfile(path):
        zf = zipfile.ZipFile(path)
        # The file is localised: export.xml, 导出.xml, 書き出し.xml ... pick the biggest
        # XML that is not the clinical-document one.
        xmls = [i for i in zf.infolist() if i.filename.lower().endswith(".xml")
                and "cda" not in Path(i.filename).name.lower()]
        if not xmls:
            raise ValueError("no Health export XML inside the zip")
        info = max(xmls, key=lambda i: i.file_size)
        base = info.filename.rsplit("/", 1)[0] + "/" if "/" in info.filename else ""
        return zf.open(info), zf, base
    return open(path, "rb"), None, ""


def _num(s: str | None) -> float | None:
    try:
        return float(s) if s not in (None, "") else None
    except ValueError:
        return None


def _quantity(text: str | None) -> tuple[float | None, str]:
    """Metadata values look like "1234 cm" or "68 degF"."""
    if not text:
        return None, ""
    parts = text.split(" ", 1)
    return _num(parts[0]), parts[1] if len(parts) > 1 else ""


class _Importer:
    def __init__(self, conn, actor: str, files_dir: Path, progress=None):
        self.conn, self.actor, self.files_dir, self.progress = conn, actor, files_dir, progress
        self.tz = profile.timezone_of(conn)
        self.now = utc_now()
        self.cache: dict = {}
        self.rows: list[tuple] = []
        self.routes: list[tuple[str, str]] = []
        self.stats = {"samples_read": 0, "samples_added": 0, "workouts": 0, "activity_days": 0,
                      "routes": 0, "skipped_types": {}, "bad_values": 0}

    # --- samples ---------------------------------------------------------------------------
    def sample(self, name: str, value: float, unit: str | None, a, meta=None,
               aggregation: str | None = None) -> None:
        m = ensure_metric(self.conn, name, unit, aggregation=aggregation, cache=self.cache)
        s_iso, s_ts, s_date = apple(a["startDate"])
        e_iso, e_ts, e_date = apple(a["endDate"])
        self.rows.append(row_for(m, value, unit, s_iso, s_ts, s_date, e_iso, e_ts, e_date,
                                 f"{SOURCE}:{a.get('sourceName', '')}", meta, self.actor,
                                 self.now))
        self.stats["samples_read"] += 1

    def record(self, elem) -> None:
        a = elem.attrib
        t = a.get("type", "")
        if t.startswith("HKQuantityTypeIdentifier"):
            value = _num(a.get("value"))
            if value is None:
                self.stats["bad_values"] += 1
                return
            suffix = t.removeprefix("HKQuantityTypeIdentifier")
            if a.get("unit") == "%":
                value *= 100  # the export writes every percentage as a fraction (0.97)
            self.sample(mx.from_hk(t), value, a.get("unit"), a,
                        aggregation=mx.guess_aggregation(mx.from_hk(t), suffix))
            return
        if not t.startswith("HKCategoryTypeIdentifier") or "startDate" not in a:
            self.stats["skipped_types"][t] = self.stats["skipped_types"].get(t, 0) + 1
            return
        v = a.get("value", "")
        minutes = (apple(a["endDate"])[1] - apple(a["startDate"])[1]) / 60
        if t == "HKCategoryTypeIdentifierSleepAnalysis":
            if v in SLEEP:
                self.sample(SLEEP[v], minutes, "min", a)
        elif t == "HKCategoryTypeIdentifierAppleStandHour":
            self.sample("stand_hour", 1.0 if v.endswith("Stood") else 0.0, "count", a)
        elif t == "HKCategoryTypeIdentifierMindfulSession":
            self.sample("mindful_minutes", minutes, "min", a)
        else:
            # Event-like categories (high heart rate alerts, handwashing ...): count events,
            # keep the value and duration in meta.
            meta = {"value": v.removeprefix("HKCategoryValue"), "duration_min": round(minutes, 2)}
            self.sample(mx.from_hk(t), 1.0, "count", a, meta=meta, aggregation="sum")

    # --- workouts and rings ----------------------------------------------------------------
    def workout(self, elem) -> None:
        a = elem.attrib
        kind = a.get("workoutActivityType", "").removeprefix("HKWorkoutActivityType")
        s_iso, s_ts, s_date = apple(a["startDate"])
        e_iso, _e_ts, _ = apple(a["endDate"])
        data: dict = {"activity_type": activity_type(kind) or "other", "activity_name": kind,
                      "device_source": a.get("sourceName", ""), "import_via": "export_xml"}
        duration = _num(a.get("duration"))
        if duration is not None:
            unit = a.get("durationUnit", "min")
            data["duration_min"] = round(duration * {"s": 1 / 60, "hr": 60}.get(unit, 1), 2)
        legacy = (("totalDistance", "totalDistanceUnit", "distance_km", "km"),
                  ("totalEnergyBurned", "totalEnergyBurnedUnit", "active_kcal", "kcal"))
        for attr, unit_attr, key, target in legacy:
            v = _num(a.get(attr))
            if v:
                data[key] = round(mx.convert("", v, a.get(unit_attr, target), target)[0], 2)
        meta, other, route = {}, {}, None
        for child in elem:
            ca = child.attrib
            if child.tag == "WorkoutStatistics":
                suffix = ca.get("type", "").removeprefix("HKQuantityTypeIdentifier")
                if suffix in WORKOUT_STATS:
                    _, key, target = WORKOUT_STATS[suffix]
                    v = _num(ca.get("sum"))
                    if v is not None:
                        data[key] = round(mx.convert("", v, ca.get("unit", target), target)[0], 2)
                elif suffix == "HeartRate":
                    for src, key in (("average", "avg_hr"), ("minimum", "min_hr"),
                                     ("maximum", "max_hr")):
                        if _num(ca.get(src)) is not None:
                            data[key] = round(float(ca[src]), 1)
                else:
                    other[mx.snake(suffix)] = {k: (_num(v) if k != "unit" else v)
                                               for k, v in ca.items()
                                               if k in ("sum", "average", "minimum", "maximum",
                                                        "unit")}
            elif child.tag == "MetadataEntry":
                meta[ca.get("key", "")] = ca.get("value", "")
            elif child.tag == "WorkoutRoute":
                ref = child.find("FileReference")
                if ref is not None and ref.get("path"):
                    route = ref.get("path")
        if meta.get("HKIndoorWorkout") in ("0", "1"):
            data["indoor"] = meta["HKIndoorWorkout"] == "1"
        elev, unit = _quantity(meta.get("HKElevationAscended"))
        if elev is not None:
            data["elevation_up_m"] = round(mx.convert("", elev, unit, "m")[0] if unit != "cm"
                                           else elev / 100, 1)
        if other:
            data["other_stats"] = other
        if meta:
            data["metadata"] = meta
        rec, _, created = records.upsert(self.conn, {
            "category": "workout", "start": s_iso, "end": e_iso, "local_date": s_date,
            "data": data, "source": SOURCE, "external_id": workout_key(s_ts)},
            self.actor, self.tz, log=False)
        self.stats["workouts"] += 1
        if route:
            self.routes.append((rec["id"], route))

    def activity(self, elem) -> None:
        a = elem.attrib
        day = a.get("dateComponents")
        fields = {"move_kcal": "activeEnergyBurned", "move_goal_kcal": "activeEnergyBurnedGoal",
                  "move_time_min": "appleMoveTime", "move_time_goal_min": "appleMoveTimeGoal",
                  "exercise_min": "appleExerciseTime",
                  "exercise_goal_min": "appleExerciseTimeGoal",
                  "stand_hours": "appleStandHours", "stand_goal_hours": "appleStandHoursGoal"}
        data = {k: round(v, 1) for k, src in fields.items()
                if (v := _num(a.get(src))) is not None}
        if not day or not any(data.get(k) for k in ("move_kcal", "exercise_min", "stand_hours")):
            return
        records.upsert(self.conn, {
            "category": "activity_summary", "start": day, "local_date": day, "data": data,
            "source": SOURCE, "external_id": f"activity:{day}"}, self.actor, self.tz, log=False)
        self.stats["activity_days"] += 1

    def me(self, elem) -> None:
        a = elem.attrib
        facts = {
            "birthday": a.get("HKCharacteristicTypeIdentifierDateOfBirth") or None,
            "sex": SEX.get(a.get("HKCharacteristicTypeIdentifierBiologicalSex", "")),
            "blood_type": BLOOD.get(a.get("HKCharacteristicTypeIdentifierBloodType", "")),
        }
        facts = {k: v for k, v in facts.items() if v}
        if facts:
            profile.patch(self.conn, facts, self.actor, only_missing=True, in_tx=True)

    # --- driver ----------------------------------------------------------------------------
    def flush(self) -> None:
        if self.rows:
            before = self.conn.total_changes
            self.conn.executemany(INSERT_SQL, self.rows)
            self.stats["samples_added"] += self.conn.total_changes - before
            self.rows.clear()

    def run(self, path: Path) -> dict:
        stream, zf, base = _open(path)
        handlers = {"Record": self.record, "Workout": self.workout,
                    "ActivitySummary": self.activity, "Me": self.me}
        try:
            with stream, Batch(self.conn) as batch:
                depth, root = 0, None
                for event, elem in ET.iterparse(stream, events=("start", "end")):
                    if event == "start":
                        depth += 1
                        if root is None:
                            root = elem
                        continue
                    depth -= 1
                    if depth != 1:
                        continue
                    if elem.tag == "Correlation":
                        for child in elem.iter("Record"):
                            self.record(child)
                    elif elem.tag == "ExportDate":
                        self.stats["export_date"] = elem.get("value")
                    elif elem.tag in handlers:
                        handlers[elem.tag](elem)
                    root.clear()
                    if len(self.rows) >= 5000:
                        n = len(self.rows)
                        self.flush()
                        if batch.tick(n) and self.progress:
                            self.progress(self.stats)
                    elif elem.tag != "Record":
                        batch.tick()
                self.flush()
                if zf is not None:
                    self.attach_routes(zf, base)
        finally:
            if zf is not None:
                zf.close()
        return self.stats

    def attach_routes(self, zf: zipfile.ZipFile, base: str) -> None:
        names = set(zf.namelist())
        for record_id, ref in self.routes:
            member = base + ref.lstrip("/")
            filename = Path(ref).name
            if member not in names or has_attachment(self.conn, record_id, filename):
                continue
            records.add_attachment(self.conn, self.files_dir, record_id, filename,
                                   "application/gpx+xml", zf.read(member), self.actor, log=False)
            self.stats["routes"] += 1


def run(conn, path: Path, actor: str, files_dir: Path, progress=None) -> dict:
    return _Importer(conn, actor, files_dir, progress).run(Path(path))
