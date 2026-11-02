"""Importers for data that arrives in bulk rather than from a conversation."""
from datetime import UTC, datetime
from xml.sax.saxutils import escape


class Batch:
    """One write transaction per `size` operations, so a long import never holds the
    database lock for more than a moment and API writes can interleave."""

    def __init__(self, conn, size: int = 20000):
        self.conn, self.size, self.n = conn, size, 0
        conn.execute("BEGIN IMMEDIATE")

    def tick(self, n: int = 1) -> bool:
        self.n += n
        if self.n < self.size:
            return False
        self.conn.execute("COMMIT")
        self.conn.execute("BEGIN IMMEDIATE")
        self.n = 0
        return True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, *_):
        self.conn.execute("ROLLBACK" if exc_type else "COMMIT")
        return False


def workout_key(start_ts: float) -> str:
    """Same workout from export.xml and Health Auto Export lands on the same record."""
    return "workout:" + datetime.fromtimestamp(start_ts, UTC).strftime("%Y-%m-%dT%H:%MZ")


def activity_type(name: str) -> str:
    from ..metrics import snake
    s = snake(name)
    for prefix in ("outdoor_", "indoor_", "pool_", "open_water_"):
        s = s.removeprefix(prefix)
    return {"walk": "walking", "run": "running", "cycle": "cycling", "swim": "swimming",
            "hike": "hiking", "hiit": "high_intensity_interval_training"}.get(s, s)


def gpx(name: str, points: list[tuple]) -> bytes:
    """points: (lat, lon, elevation_m | None, iso_time | None)."""
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<gpx version="1.1" creator="lifeos" xmlns="http://www.topografix.com/GPX/1/1">',
           f"<trk><name>{escape(name)}</name><trkseg>"]
    for lat, lon, ele, t in points:
        inner = (f"<ele>{ele}</ele>" if ele is not None else "") + (
            f"<time>{escape(t)}</time>" if t else "")
        out.append(f'<trkpt lat="{lat}" lon="{lon}">{inner}</trkpt>')
    out.append("</trkseg></trk></gpx>")
    return "\n".join(out).encode()


def has_attachment(conn, record_id: str, filename: str) -> bool:
    return conn.execute("SELECT 1 FROM attachments WHERE record_id = ? AND filename = ?"
                        " AND deleted_at IS NULL", (record_id, filename)).fetchone() is not None
