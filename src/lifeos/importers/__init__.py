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


# Health Auto Export names workouts in the phone's language ("室内 步行"). Longer words first,
# so 功能性力量训练 is not taken for plain 力量训练.
LOCALIZED_ACTIVITIES = (
    ("高强度间歇", "high_intensity_interval_training"), ("功能性力量", "functional_strength_training"),
    ("传统力量", "traditional_strength_training"), ("力量训练", "traditional_strength_training"),
    ("核心训练", "core_training"), ("混合有氧", "mixed_cardio"), ("整理放松", "cooldown"),
    ("椭圆机", "elliptical"), ("划船", "rowing"), ("爬楼", "stair_climbing"),
    ("楼梯", "stair_climbing"), ("普拉提", "pilates"), ("瑜伽", "yoga"), ("舞蹈", "dance"),
    ("太极", "tai_chi"), ("柔韧", "flexibility"), ("徒步", "hiking"), ("远足", "hiking"),
    ("步行", "walking"), ("跑步", "running"), ("骑行", "cycling"), ("骑车", "cycling"),
    ("自行车", "cycling"), ("游泳", "swimming"), ("羽毛球", "badminton"),
    ("乒乓球", "table_tennis"), ("篮球", "basketball"), ("足球", "soccer"), ("网球", "tennis"),
    ("排球", "volleyball"), ("滑雪", "skiing"), ("攀岩", "climbing"), ("拳击", "boxing"),
    ("跳绳", "jump_rope"), ("ウォーキング", "walking"), ("ランニング", "running"),
    ("サイクリング", "cycling"), ("スイミング", "swimming"), ("水泳", "swimming"),
    ("ハイキング", "hiking"), ("ヨガ", "yoga"),
)


def activity_type(name: str) -> str:
    from ..metrics import snake
    s = snake(name)
    for prefix in ("outdoor_", "indoor_", "pool_", "open_water_"):
        s = s.removeprefix(prefix)
    if s:
        return {"walk": "walking", "run": "running", "cycle": "cycling", "swim": "swimming",
                "hike": "hiking", "hiit": "high_intensity_interval_training"}.get(s, s)
    return next((kind for word, kind in LOCALIZED_ACTIVITIES if word in name), "other")


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
