"""Files that arrive whole (an export from the phone): recognise, file under raw/, import."""
import json
import shutil
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from . import jobs
from .config import Settings
from .db import log_change

KINDS = ("apple-health", "health-auto-export", "health-auto-export-csv")


def detect(path: Path) -> str | None:
    """Which importer reads this file, judged by its contents rather than its (localised) name."""
    if path.suffix.lower() == ".json":
        return "health-auto-export"
    if path.suffix.lower() == ".xml":
        return "apple-health"
    if not zipfile.is_zipfile(path):
        return None
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
    if any(n.lower().endswith(".xml") and "cda" not in Path(n).name.lower() for n in names):
        return "apple-health"
    if any(Path(n).name.startswith(("Medications", "HealthAutoExport-")) for n in names):
        return "health-auto-export-csv"
    return None


def file_away(settings: Settings, kind: str, path: Path) -> Path:
    """Move an arrived file to where lifeos keeps originals, and return its new path."""
    now = datetime.now(UTC)
    if kind == "apple-health":
        dest = settings.raw_dir / "apple-health" / f"{now:%Y%m%dT%H%M%SZ}-{path.name}"
    else:
        dest = settings.raw_dir / "health-auto-export" / f"{now:%Y-%m}" / path.name
        if dest.exists():
            dest = dest.with_name(f"{now:%Y%m%dT%H%M%SZ}-{path.name}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(path, dest)
    return dest


def import_file(conn, settings: Settings, kind: str, path: Path, actor: str,
                progress=None) -> dict:
    """Run the importer for `kind` on a file already kept under raw/, and record the import."""
    if kind == "apple-health":
        from .importers import apple_health
        stats = apple_health.run(conn, path, actor, settings.files_dir, progress)
    elif kind == "health-auto-export-csv":
        from .importers import hae_csv
        stats = hae_csv.run(conn, path, actor)
    elif kind == "health-auto-export":
        from .importers import health_auto_export
        stats = health_auto_export.run(conn, json.loads(path.read_text()), actor,
                                       settings.files_dir)
    else:
        raise ValueError(f"unknown import kind {kind!r}")
    job_id = jobs.record(conn, kind.replace("-", "_"), str(path), actor, "done", stats)
    log_change(conn, actor, "import", "import", job_id, after=stats)
    return stats
