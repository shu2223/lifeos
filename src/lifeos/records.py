"""Records: structured, timestamped entries such as meals, workouts, moods and diary notes."""
import copy
import hashlib
import json
import mimetypes
import re
from pathlib import Path

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from . import categories as cats
from . import series
from .db import dumps, loads, log_change, new_id, tx
from .errors import ApiError, bad_request, not_found
from .timeutil import iso, parse, utc_now

MAX_LIMIT = 500
_validators: dict[tuple[str, str], Draft202012Validator] = {}


def _row(r) -> dict:
    return {
        "id": r["id"], "category": r["category"], "start": r["start_at"], "end": r["end_at"],
        "local_date": r["local_date"], "title": r["title"], "data": loads(r["data"]),
        "tags": loads(r["tags"]), "note": r["note"], "source": r["source"],
        "external_id": r["external_id"], "created_by": r["created_by"],
        "updated_by": r["updated_by"], "created_at": r["created_at"],
        "updated_at": r["updated_at"], "deleted_at": r["deleted_at"],
    }


def category(conn, name: str) -> dict:
    r = conn.execute("SELECT * FROM categories WHERE name = ?", (name,)).fetchone()
    if r is None:
        names = [x["name"] for x in conn.execute("SELECT name FROM categories ORDER BY name")]
        raise not_found("category", name,
                        f"existing: {', '.join(names)}. Create one with POST /api/v1/categories")
    return dict(r)


def validator(cat: dict) -> Draft202012Validator:
    key = (cat["name"], cat["schema"])
    v = _validators.get(key)
    if v is None:
        schema = json.loads(cat["schema"])
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as e:
            raise bad_request(f"invalid JSON Schema: {e.message}") from None
        v = _validators[key] = Draft202012Validator(schema)
    return v


def _validate(cat: dict, data: dict) -> None:
    errors = sorted(validator(cat).iter_errors(data), key=lambda e: list(e.absolute_path))
    if errors:
        detail = "; ".join(
            f"data{''.join(f'[{p!r}]' for p in e.absolute_path)}: {e.message}" for e in errors[:5])
        raise ApiError(422, "invalid_data", detail,
                       f"GET /api/v1/categories/{cat['name']} shows the schema and an example")


def _times(body: dict, tz):
    try:
        start = parse(body.get("start"), tz)
        end = parse(body["end"], tz) if body.get("end") else None
    except ValueError as e:
        raise bad_request(str(e)) from None
    if end is not None and end < start:
        raise bad_request("end is before start")
    return start, end


def _prepared(conn, body: dict, tz, warnings: list[str]) -> dict:
    cat = category(conn, body.get("category") or "")
    data = copy.deepcopy(body.get("data") or {})
    if not isinstance(data, dict):
        raise bad_request("data must be a JSON object")
    sent_totals = None
    if cat["name"] == "meal":
        sent_totals = data.pop("totals", None)
        cats.canonical_meal(data, warnings)
    elif cat["name"] == "workout":
        cats.canonical_workout(data, warnings)
    start, end = _times(body, tz)
    _validate(cat, data)
    data = cats.derive(conn, cat["name"], data, start, end, warnings)
    if isinstance(sent_totals, dict) and any(v != data["totals"].get(k) for k, v in sent_totals.items()):
        warnings.append(f"data.totals is computed from the items, which add up to "
                        f"{data['totals'].get('kcal', 0):g} kcal; the totals sent were not used. "
                        "Put the nutrients on each item")
    tags = body.get("tags") or []
    if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
        raise bad_request("tags must be a list of strings")
    title = (body.get("title") or "").strip() or cats.default_title(cat["name"], cat["title"], data)
    return {
        "category": cat["name"], "start_at": iso(start), "end_at": iso(end) if end else None,
        "start_ts": start.timestamp(), "end_ts": end.timestamp() if end else None,
        "local_date": body.get("local_date") or start.date().isoformat(), "title": title,
        "data": dumps(data), "tags": dumps(tags), "note": body.get("note") or "",
    }


def _sent_data(category: str, body: dict, warnings: list[str]):
    """The data an update sends, with workout aliases renamed before it is merged, so that
    "calories" replaces a stored active_kcal instead of losing to it."""
    sent = copy.deepcopy(body.get("data", {}))
    if category == "workout" and isinstance(sent, dict):
        cats.canonical_workout(sent, warnings)
    return sent


def _retimed(before: dict, times: dict, tz) -> bool:
    """Whether `times` gives the stored record `before` a new span that has an end."""
    start, end = _times(times, tz)
    return end is not None and (start, end) != _times(before, tz)


def _own_data(rec: dict, sent: dict, retimed: bool) -> dict:
    """A stored record's data without what the server derives, to merge `sent` into: any meal
    totals in the result were then sent by the caller, and a workout given new times gets its
    duration from them, unless `sent` has one."""
    derived = {"meal": {"totals"},
               "workout": {"duration_min"} if retimed and "duration_min" not in (sent or {}) else set()}
    return {k: v for k, v in rec["data"].items() if k not in derived.get(rec["category"], ())}


def get(conn, record_id: str, with_attachments: bool = True) -> dict:
    r = conn.execute("SELECT * FROM records WHERE id = ?", (record_id,)).fetchone()
    if r is None:
        raise not_found("record", record_id)
    rec = _row(r)
    if with_attachments:
        rec["attachments"] = attachments_of(conn, record_id)
        rec["series"] = series.listing(conn, record_id)
    return rec


def upsert(conn, body: dict, actor: str, tz, *, log: bool = True) -> tuple[dict, list[str], bool]:
    """Create a record, or update the one with the same (source, category, external_id).

    An update refreshes the fields the sender provides and keeps what others added: data keys
    it does not send (an rpe the user gave an imported workout), the note, tags and a custom
    title. A resend without start or end keeps the stored times. A record the user deleted
    stays deleted. The caller holds the transaction.
    Returns (record, warnings, created)."""
    warnings: list[str] = []
    source = (body.get("source") or "agent").strip()
    ext = body.get("external_id")
    now = utc_now()
    existing = None
    if ext:
        existing = conn.execute(
            "SELECT * FROM records WHERE source = ? AND category = ? AND external_id = ?",
            (source, body.get("category"), str(ext))).fetchone()
    if existing:
        before = _row(existing)
        cat = category(conn, before["category"])
        auto_title = cats.default_title(cat["name"], cat["title"], before["data"])
        sent = _sent_data(before["category"], body, warnings)
        times = {"start": body.get("start") or before["start"],
                 "end": body.get("end") if body.get("start") or body.get("end") else before["end"]}
        f = _prepared(conn, {
            **body, **times,
            "data": {**_own_data(before, sent, _retimed(before, times, tz)), **(sent or {})},
            "note": body.get("note") or before["note"],
            "tags": body.get("tags") or before["tags"],
            "title": body.get("title") or ("" if before["title"] == auto_title
                                           else before["title"]),
        }, tz, warnings)
        conn.execute(
            "UPDATE records SET start_at=:start_at, end_at=:end_at, start_ts=:start_ts,"
            " end_ts=:end_ts, local_date=:local_date, title=:title, data=:data, tags=:tags,"
            " note=:note, updated_by=:actor, updated_at=:now WHERE id=:id",
            {**f, "actor": actor, "now": now, "id": existing["id"]})
        rec = get(conn, existing["id"], with_attachments=False)
        if log and {**before, "updated_at": None, "updated_by": None} != {
                **rec, "updated_at": None, "updated_by": None}:
            log_change(conn, actor, "update", "record", rec["id"], before=before, after=rec, at=now)
        return rec, warnings, False
    f = _prepared(conn, body, tz, warnings)
    rid = new_id("rec")
    conn.execute(
        "INSERT INTO records(id, category, start_at, end_at, start_ts, end_ts, local_date, title,"
        " data, tags, note, source, external_id, created_by, updated_by, created_at, updated_at)"
        " VALUES (:id, :category, :start_at, :end_at, :start_ts, :end_ts, :local_date, :title,"
        " :data, :tags, :note, :source, :ext, :actor, :actor, :now, :now)",
        {**f, "id": rid, "source": source, "ext": str(ext) if ext else None,
         "actor": actor, "now": now})
    rec = get(conn, rid, with_attachments=False)
    if log:
        log_change(conn, actor, "create", "record", rid, after=rec, at=now)
    return rec, warnings, True


def create(conn, body: dict, actor: str, tz) -> tuple[dict, list[str], bool]:
    with tx(conn):
        return upsert(conn, body, actor, tz)


def merge_patch(target, patch):
    """RFC 7386 JSON merge patch: objects merge, null deletes, anything else replaces."""
    if not isinstance(patch, dict):
        return copy.deepcopy(patch)
    out = dict(target) if isinstance(target, dict) else {}
    for k, v in patch.items():
        if v is None:
            out.pop(k, None)
        else:
            out[k] = merge_patch(out.get(k), v)
    return out


def update(conn, record_id: str, patch: dict, actor: str, tz) -> tuple[dict, list[str]]:
    unknown = set(patch) - {"category", "start", "end", "title", "data", "tags", "note"}
    if unknown:
        raise bad_request(f"cannot patch {sorted(unknown)}",
                          "patchable: category, start, end, title, data, tags, note")
    if "start" in patch and patch["start"] is None:
        raise bad_request("start cannot be removed", "send a new start, or leave it out")
    with tx(conn):
        before = get(conn, record_id, with_attachments=False)
        if before["deleted_at"]:
            raise ApiError(409, "deleted", f"record {record_id} is deleted",
                           f"POST /api/v1/records/{record_id}/restore first")
        cat = category(conn, before["category"])
        auto_title = cats.default_title(cat["name"], cat["title"], before["data"])
        warnings: list[str] = []
        sent = _sent_data(patch.get("category", before["category"]), patch, warnings)
        times = {"start": patch.get("start", before["start"]),
                 "end": patch["end"] if "end" in patch else before["end"]}
        body = {
            "category": patch.get("category", before["category"]), **times,
            "data": merge_patch(_own_data(before, sent, _retimed(before, times, tz)), sent),
            "tags": patch.get("tags", before["tags"]),
            "note": patch.get("note", before["note"]),
            "title": patch.get("title", "" if before["title"] == auto_title else before["title"]),
            "local_date": None,
        }
        f = _prepared(conn, body, tz, warnings)
        now = utc_now()
        conn.execute(
            "UPDATE records SET category=:category, start_at=:start_at, end_at=:end_at,"
            " start_ts=:start_ts, end_ts=:end_ts, local_date=:local_date, title=:title,"
            " data=:data, tags=:tags, note=:note, updated_by=:actor, updated_at=:now"
            " WHERE id=:id", {**f, "actor": actor, "now": now, "id": record_id})
        after = get(conn, record_id)
        log_change(conn, actor, "update", "record", record_id, before=before,
                   after={k: v for k, v in after.items() if k != "attachments"}, at=now)
    return after, warnings


def set_deleted(conn, record_id: str, actor: str, deleted: bool) -> dict:
    with tx(conn):
        before = get(conn, record_id, with_attachments=False)
        if bool(before["deleted_at"]) == deleted:
            return before
        conn.execute("UPDATE records SET deleted_at = ?, updated_by = ?, updated_at = ?"
                     " WHERE id = ?", (utc_now() if deleted else None, actor, utc_now(), record_id))
        log_change(conn, actor, "delete" if deleted else "restore", "record", record_id,
                   before=before)
        return get(conn, record_id)


def search(conn, *, categories=None, date=None, date_from=None, date_to=None, ts_from=None,
           ts_to=None, q=None, tag=None, source=None, include_deleted=False, limit=50,
           cursor=None, order="desc") -> dict:
    where, args = [], []
    if not include_deleted:
        where.append("deleted_at IS NULL")
    if categories:
        where.append(f"category IN ({','.join('?' * len(categories))})")
        args += categories
    if date:
        where.append("local_date = ?")
        args.append(date)
    if date_from:
        where.append("local_date >= ?")
        args.append(date_from)
    if date_to:
        where.append("local_date <= ?")
        args.append(date_to)
    if ts_from is not None:
        where.append("start_ts >= ?")
        args.append(ts_from)
    if ts_to is not None:
        where.append("start_ts < ?")
        args.append(ts_to)
    if q:
        where.append("(title LIKE ? OR note LIKE ? OR data LIKE ?)")
        args += [f"%{q}%"] * 3
    if tag:
        where.append("EXISTS (SELECT 1 FROM json_each(records.tags) WHERE value = ?)")
        args.append(tag)
    if source:
        where.append("source = ?")
        args.append(source)
    desc = order != "asc"
    if cursor:
        try:
            ts, rid = cursor.split("~", 1)
            ts = float(ts)
        except ValueError:
            raise bad_request("bad cursor; pass next_cursor from the previous page") from None
        where.append("(start_ts, id) < (?, ?)" if desc else "(start_ts, id) > (?, ?)")
        args += [ts, rid]
    limit = max(1, min(int(limit), MAX_LIMIT))
    sql = ("SELECT * FROM records" + (" WHERE " + " AND ".join(where) if where else "")
           + f" ORDER BY start_ts {'DESC' if desc else 'ASC'}, id {'DESC' if desc else 'ASC'}"
           + " LIMIT ?")
    rows = conn.execute(sql, [*args, limit + 1]).fetchall()
    recs = [_row(r) for r in rows[:limit]]
    nxt = f"{rows[limit - 1]['start_ts']}~{rows[limit - 1]['id']}" if len(rows) > limit else None
    return {"records": recs, "next_cursor": nxt}


# --- attachments ---------------------------------------------------------------------------

def attachments_of(conn, record_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT id, filename, mime, size, sha256, created_by, created_at FROM attachments"
        " WHERE record_id = ? AND deleted_at IS NULL ORDER BY created_at", (record_id,))
    return [dict(r) for r in rows]


def _safe_name(name: str) -> str:
    name = Path(name or "file").name
    return re.sub(r"[^\w.\-]+", "_", name)[-120:] or "file"


def add_attachment(conn, files_dir: Path, record_id: str, filename: str, mime: str | None,
                   content: bytes, actor: str, *, log: bool = True) -> dict:
    get(conn, record_id, with_attachments=False)
    aid, now = new_id("att"), utc_now()
    rel = Path(now[:4]) / now[5:7] / f"{aid}-{_safe_name(filename)}"
    dest = files_dir / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(content)
    mime = mime or mimetypes.guess_type(filename)[0] or "application/octet-stream"
    row = {"id": aid, "record_id": record_id, "filename": filename, "mime": mime,
           "size": len(content), "sha256": hashlib.sha256(content).hexdigest(),
           "path": str(rel), "created_by": actor, "created_at": now}
    conn.execute("INSERT INTO attachments(id, record_id, filename, mime, size, sha256, path,"
                 " created_by, created_at) VALUES (:id, :record_id, :filename, :mime, :size,"
                 " :sha256, :path, :created_by, :created_at)", row)
    if log:
        log_change(conn, actor, "create", "attachment", aid, after=row, at=now)
    return {k: v for k, v in row.items() if k != "path"}


def attachment(conn, attachment_id: str) -> dict:
    r = conn.execute("SELECT * FROM attachments WHERE id = ? AND deleted_at IS NULL",
                     (attachment_id,)).fetchone()
    if r is None:
        raise not_found("attachment", attachment_id)
    return dict(r)
