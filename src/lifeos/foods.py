"""The personal food library: foods the user eats often, with nutrition per serving or 100 g.

It grows as agents log meals, so the second "一碗牛肉面" reuses the first estimate.
"""
from .categories import NUTRIENTS
from .db import dumps, loads, log_change, new_id, tx
from .errors import bad_request, not_found
from .timeutil import utc_now

FIELDS = ("name", "aliases", "brand", "serving", "per_serving", "per_100g", "note", "source")
SOURCES = ("estimate", "label", "user", "database")


def _row(r) -> dict:
    return {
        "id": r["id"], "name": r["name"], "aliases": loads(r["aliases"]), "brand": r["brand"],
        "serving": loads(r["serving"]), "per_serving": loads(r["per_serving"]),
        "per_100g": loads(r["per_100g"]), "note": r["note"], "source": r["source"],
        "use_count": r["use_count"], "last_used_at": r["last_used_at"],
        "created_by": r["created_by"], "created_at": r["created_at"],
        "updated_at": r["updated_at"],
    }


def _check(food: dict) -> None:
    if not str(food.get("name", "")).strip():
        raise bad_request("food needs a name")
    if not (food.get("per_serving") or food.get("per_100g")):
        raise bad_request("food needs per_serving or per_100g nutrients",
                          "e.g. per_100g: {\"kcal\": 156, \"protein_g\": 12.6}")
    if food.get("per_serving") and not (food.get("serving") or {}).get("unit"):
        raise bad_request("per_serving needs serving: {amount, unit, grams?}",
                          "e.g. serving: {\"amount\": 1, \"unit\": \"碗\", \"grams\": 550}")
    for key in ("per_serving", "per_100g"):
        unknown = set(food.get(key) or {}) - set(NUTRIENTS)
        if unknown:
            raise bad_request(f"{key} has unknown nutrient keys: {sorted(unknown)}",
                              f"known keys: {', '.join(NUTRIENTS)}")
    if food.get("source", "estimate") not in SOURCES:
        raise bad_request(f"source must be one of {', '.join(SOURCES)}")


def get_food(conn, food_id: str) -> dict | None:
    r = conn.execute("SELECT * FROM foods WHERE id = ? AND deleted_at IS NULL",
                     (food_id,)).fetchone()
    return _row(r) if r else None


def touch_food(conn, food_id: str) -> None:
    conn.execute("UPDATE foods SET use_count = use_count + 1, last_used_at = ? WHERE id = ?",
                 (utc_now(), food_id))


def search(conn, q: str | None, limit: int) -> list[dict]:
    if q:
        like = f"%{q.strip()}%"
        rows = conn.execute(
            "SELECT * FROM foods WHERE deleted_at IS NULL"
            " AND (name LIKE ? OR aliases LIKE ? OR brand LIKE ?)"
            " ORDER BY (name = ?) DESC, use_count DESC LIMIT ?",
            (like, like, like, q.strip(), limit)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM foods WHERE deleted_at IS NULL"
                            " ORDER BY use_count DESC, updated_at DESC LIMIT ?",
                            (limit,)).fetchall()
    return [_row(r) for r in rows]


def create(conn, body: dict, actor: str) -> dict:
    food = {k: body.get(k) for k in FIELDS}
    food["aliases"] = food["aliases"] or []
    food["source"] = food["source"] or "estimate"
    _check(food)
    now, fid = utc_now(), new_id("food")
    with tx(conn):
        conn.execute(
            "INSERT INTO foods(id, name, aliases, brand, serving, per_serving, per_100g, note,"
            " source, created_by, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (fid, food["name"].strip(), dumps(food["aliases"]), food["brand"] or "",
             dumps(food["serving"]) if food["serving"] else None,
             dumps(food["per_serving"]) if food["per_serving"] else None,
             dumps(food["per_100g"]) if food["per_100g"] else None,
             food["note"] or "", food["source"], actor, now, now))
        created = get_food(conn, fid)
        log_change(conn, actor, "create", "food", fid, after=created, at=now)
    return created


def update(conn, food_id: str, body: dict, actor: str) -> dict:
    with tx(conn):
        before = get_food(conn, food_id)
        if before is None:
            raise not_found("food", food_id)
        food = {k: body[k] if k in body else before[k] for k in FIELDS}
        _check(food)
        conn.execute(
            "UPDATE foods SET name=?, aliases=?, brand=?, serving=?, per_serving=?, per_100g=?,"
            " note=?, source=?, updated_at=? WHERE id=?",
            (food["name"].strip(), dumps(food["aliases"] or []), food["brand"] or "",
             dumps(food["serving"]) if food["serving"] else None,
             dumps(food["per_serving"]) if food["per_serving"] else None,
             dumps(food["per_100g"]) if food["per_100g"] else None,
             food["note"] or "", food["source"], utc_now(), food_id))
        after = get_food(conn, food_id)
        log_change(conn, actor, "update", "food", food_id, before=before, after=after)
    return after


def delete(conn, food_id: str, actor: str) -> None:
    with tx(conn):
        before = get_food(conn, food_id)
        if before is None:
            raise not_found("food", food_id)
        conn.execute("UPDATE foods SET deleted_at = ? WHERE id = ?", (utc_now(), food_id))
        log_change(conn, actor, "delete", "food", food_id, before=before)
