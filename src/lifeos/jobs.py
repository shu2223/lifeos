"""Background imports. One at a time, each on its own database connection."""
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .config import Settings
from .db import connect, dumps, loads, log_change, new_id
from .importers import apple_health
from .timeutil import utc_now

log = logging.getLogger("lifeos.jobs")


def _row(r) -> dict:
    return {"id": r["id"], "kind": r["kind"], "status": r["status"], "file": r["file"],
            "stats": loads(r["stats"]), "error": r["error"], "created_by": r["created_by"],
            "created_at": r["created_at"], "finished_at": r["finished_at"]}


def get(conn, job_id: str) -> dict | None:
    r = conn.execute("SELECT * FROM imports WHERE id = ?", (job_id,)).fetchone()
    return _row(r) if r else None


def listing(conn, limit: int = 20) -> list[dict]:
    return [_row(r) for r in conn.execute(
        "SELECT * FROM imports ORDER BY created_at DESC LIMIT ?", (limit,))]


def record(conn, kind: str, file: str | None, actor: str, status: str = "queued",
           stats: dict | None = None) -> str:
    job_id = new_id("imp")
    conn.execute("INSERT INTO imports(id, kind, status, file, stats, created_by, created_at,"
                 " finished_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                 (job_id, kind, status, file, dumps(stats or {}), actor, utc_now(),
                  utc_now() if status in ("done", "failed") else None))
    return job_id


class Jobs:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="lifeos-import")
        self.lock = threading.Lock()

    def interrupted(self, conn) -> None:
        """Imports that were running when the service stopped will not finish on their own."""
        conn.execute("UPDATE imports SET status = 'failed', error = 'interrupted by restart',"
                     " finished_at = ? WHERE status IN ('queued', 'running')", (utc_now(),))

    def submit_apple_health(self, conn, path: Path, actor: str) -> str:
        job_id = record(conn, "apple_health", str(path), actor)
        self.pool.submit(self._run_apple_health, job_id, path, actor)
        return job_id

    def _run_apple_health(self, job_id: str, path: Path, actor: str) -> None:
        conn = connect(self.settings.db_path)
        try:
            conn.execute("UPDATE imports SET status = 'running' WHERE id = ?", (job_id,))

            def progress(stats):
                conn.execute("UPDATE imports SET stats = ? WHERE id = ?", (dumps(stats), job_id))

            stats = apple_health.run(conn, path, actor, self.settings.files_dir, progress)
            conn.execute("UPDATE imports SET status = 'done', stats = ?, finished_at = ?"
                         " WHERE id = ?", (dumps(stats), utc_now(), job_id))
            log_change(conn, actor, "import", "import", job_id, after=stats)
        except Exception as e:
            log.exception("apple health import %s failed", job_id)
            conn.execute("UPDATE imports SET status = 'failed', error = ?, finished_at = ?"
                         " WHERE id = ?", (f"{type(e).__name__}: {e}", utc_now(), job_id))
        finally:
            conn.close()

    def shutdown(self) -> None:
        self.pool.shutdown(wait=False, cancel_futures=True)
