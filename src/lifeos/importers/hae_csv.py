"""Health Auto Export CSV export (a zip of CSV files, headers in the phone's language).

Only what Apple's own export.xml lacks is read from it: the medication log. Metrics and
workouts are better imported from export.xml, which keeps every sample with its UTC offset;
the whole zip stays in the raw folder either way.
"""
import csv
import io
import zipfile
from pathlib import Path

from .. import profile, records
from . import Batch

TAKEN = {"已服用", "Taken", "taken"}
# Column names per language: (date, scheduled date, medication, nickname, dose, unit, status)
MEDICATION_COLUMNS = (
    ("日期", "预定日期", "药物", "昵称", "剂量", "单位", "状态"),
    ("Date", "Scheduled Date", "Medication", "Nickname", "Dosage", "Unit", "Status"),
)


def _rows(zf: zipfile.ZipFile, prefix: str):
    for name in zf.namelist():
        if Path(name).name.startswith(prefix) and name.endswith(".csv"):
            with zf.open(name) as f:
                yield from csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"))


def _num(s: str | None) -> float | None:
    try:
        return float(s) if s not in (None, "") else None
    except ValueError:
        return None


def run(conn, path: Path, actor: str) -> dict:
    tz = profile.timezone_of(conn)
    stats = {"medications": 0, "medications_not_taken": 0}
    with zipfile.ZipFile(path) as zf, Batch(conn, size=10**9):
        for row in _rows(zf, "Medications"):
            cols = next((c for c in MEDICATION_COLUMNS if c[0] in row), None)
            if cols is None:
                continue
            date, scheduled, med, nick, dose, unit, status = (row.get(c, "") for c in cols)
            if not date or not med:
                continue
            if status not in TAKEN:
                stats["medications_not_taken"] += 1
                continue
            data = {"name": nick or med, "kind": "medicine"}
            if nick:
                data["medication"] = med
            if _num(dose) is not None:
                data["dose"] = _num(dose)
            if unit:
                data["unit"] = unit
            if scheduled:
                data["scheduled_at"] = scheduled
            records.upsert(conn, {
                "category": "medication", "start": date, "data": data, "source": "apple_health",
                "external_id": f"medication:{date}:{med}"}, actor, tz, log=False)
            stats["medications"] += 1
    return stats
