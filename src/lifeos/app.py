"""HTTP API. Everything an agent needs is discoverable from GET /llms.txt and GET /api/v1."""
import json
import logging
import re
from contextlib import asynccontextmanager
from importlib import resources
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, FastAPI, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__, auth, foods, jobs, profile, records, samples, series, summary
from . import categories as cats
from . import metrics as mx
from .config import Settings, from_env
from .db import connect, dumps, loads, log_change, migrate, tx
from .errors import ApiError, bad_request, not_found
from .importers import health_auto_export
from .timeutil import iso, now, parse, utc_now

log = logging.getLogger("lifeos")
API = "/api/v1"
WEB = "/web"  # the web page's own mount of the API, behind Cloudflare Access
WEB_DIR = Path(__file__).parent / "web"
ACCESS_HEADER = "cf-access-jwt-assertion"
PAGE_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; "
                               "img-src 'self' blob: data:; connect-src 'self'; "
                               "object-src 'none'; frame-ancestors 'none'; base-uri 'none'; "
                               "form-action 'self'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-cache",
}
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,47}$")
MANUAL = resources.files("lifeos").joinpath("manual.md").read_text(encoding="utf-8")


# --- request bodies (also document the API in /openapi.json) ------------------------------

class RecordIn(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={
        "examples": [cats.BUILTIN["meal"]["example"]]})
    category: str = Field(description="category name, see GET /api/v1/categories")
    start: str | None = Field(None, description="when it happened, ISO 8601; default now")
    end: str | None = Field(None, description="when it ended, for things with a duration")
    title: str | None = Field(None, description="short human summary; generated if omitted")
    data: dict[str, Any] = Field(default_factory=dict, description="fields per category schema")
    tags: list[str] = Field(default_factory=list)
    note: str | None = Field(None, description="free text, e.g. the user's own words")
    source: str | None = Field(None, description="where it came from; default 'agent'")
    external_id: str | None = Field(
        None, description="your stable id; resending the same (source, category, external_id) "
                          "updates instead of duplicating")


class RecordPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: str | None = None
    start: str | None = None
    end: str | None = None
    title: str | None = None
    data: dict[str, Any] | None = Field(
        None, description="JSON merge patch: objects merge, null deletes a key, arrays replace")
    tags: list[str] | None = None
    note: str | None = None


class RecordBatch(BaseModel):
    records: list[RecordIn] = Field(max_length=500)


class SampleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metric: str = Field(description="metric name, e.g. body_mass; unknown names are created")
    value: float
    unit: str | None = Field(None, description="default: the metric's unit")
    start: str | None = Field(None, description="ISO 8601; default now")
    end: str | None = None
    source: str | None = Field(None, description="default 'agent'")
    meta: dict[str, Any] | None = None


class SampleBatch(BaseModel):
    samples: list[SampleIn] = Field(max_length=samples.MAX_BATCH)


class MetricIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    title: str
    unit: str
    aggregation: str = Field(description="sum | avg | last")


class CategoryIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(description="snake_case, 2-48 chars")
    title: str = Field(description="display name, e.g. 读书")
    description: str = Field(description="what belongs here, written for other agents")
    data_schema: dict[str, Any] = Field(
        default_factory=lambda: {"type": "object"}, alias="schema",
        description="JSON Schema (2020-12) for data")
    example: dict[str, Any] | None = None


class CategoryPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = None
    description: str | None = None
    data_schema: dict[str, Any] | None = Field(None, alias="schema")
    example: dict[str, Any] | None = None


class FoodIn(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [{
        "name": "牛肉面", "aliases": ["兰州拉面"], "serving": {"amount": 1, "unit": "碗",
                                                         "grams": 550},
        "per_serving": {"kcal": 620, "protein_g": 30, "fat_g": 16, "carbs_g": 88},
        "source": "estimate"}]})
    name: str
    aliases: list[str] = Field(default_factory=list)
    brand: str | None = None
    serving: dict[str, Any] | None = Field(None, description="{amount, unit, grams?}")
    per_serving: dict[str, float] | None = None
    per_100g: dict[str, float] | None = None
    note: str | None = None
    source: str | None = Field(None, description="estimate | label | user | database")


class FoodPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = None
    aliases: list[str] | None = None
    brand: str | None = None
    serving: dict[str, Any] | None = None
    per_serving: dict[str, float] | None = None
    per_100g: dict[str, float] | None = None
    note: str | None = None
    source: str | None = None


class TokenIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    scope: str = "write"


# --- dependencies -----------------------------------------------------------------------

def get_db(request: Request):
    conn = connect(request.app.state.settings.db_path)
    try:
        yield conn
    finally:
        conn.close()


Db = Annotated[Any, Depends(get_db)]
_bearer = HTTPBearer(auto_error=False, description="Authorization: Bearer lifeos_...")


def _public(request: Request) -> bool:
    host = request.app.state.settings.public_host
    return bool(host) and request.headers.get("host") == host


def _web_actor(request: Request) -> auth.Actor | None:
    """The owner in their own browser on the public site. Cloudflare Access signs every request
    under the web mount in before it reaches us (the same rule as the other yishuapp.com
    pages), so the header's presence there means it is them."""
    s = request.app.state.settings
    if not (_public(request) and request.url.path.startswith(s.prefix + WEB + "/")
            and request.headers.get(ACCESS_HEADER)):
        return None
    if request.method not in ("GET", "HEAD") and \
            request.headers.get("origin") != f"https://{s.public_host}":
        raise ApiError(403, "forbidden", "refused a write from another site")
    return auth.Actor("web", "admin")


def require(scope: str):
    def dep(request: Request, conn: Db,
            creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> auth.Actor:
        web = _web_actor(request)
        if web is not None:
            return web
        token = creds.credentials if creds else request.headers.get("x-api-key")
        if not token:
            raise ApiError(401, "unauthorized", "missing token",
                           "send 'Authorization: Bearer <token>'. The owner creates one with "
                           "`lifeos token create <agent-name>` on the mini PC")
        actor = auth.verify(conn, token)
        if actor is None:
            raise ApiError(401, "unauthorized", "unknown or revoked token")
        if not actor.can(scope):
            raise ApiError(403, "forbidden", f"this token has scope {actor.scope!r}; "
                                             f"this endpoint needs {scope!r}")
        return actor
    return dep


Reader = Annotated[auth.Actor, Depends(require("read"))]
Writer = Annotated[auth.Actor, Depends(require("write"))]
Admin = Annotated[auth.Actor, Depends(require("admin"))]


def _split(value: str | None) -> list[str] | None:
    return [v.strip() for v in value.split(",") if v.strip()] if value else None


# --- app --------------------------------------------------------------------------------

def bootstrap(settings: Settings):
    """Create the data directory and database, and refresh built-in categories and metrics."""
    settings.ensure_dirs()
    conn = connect(settings.db_path)
    migrate(conn)
    with tx(conn):
        stamp = utc_now()
        cats.seed(conn, stamp)
        mx.seed(conn, stamp)
    return conn


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        conn = bootstrap(settings)
        try:
            app.state.jobs.interrupted(conn)
        finally:
            conn.close()
        yield
        app.state.jobs.shutdown()

    p = settings.prefix
    app = FastAPI(
        title="lifeos", version=__version__, lifespan=lifespan,
        description="Personal records API on the owner's mini PC. Agents: read "
                    f"{p}{API}/manual first.",
        openapi_url=f"{p}{API}/openapi.json", docs_url=f"{p}{API}/docs", redoc_url=None)
    app.state.settings = settings
    app.state.jobs = jobs.Jobs(settings)

    @app.exception_handler(ApiError)
    async def _api_error(_req, e: ApiError):
        body = {"code": e.code, "message": e.message}
        if e.hint:
            body["hint"] = e.hint
        return JSONResponse({"error": body}, status_code=e.status)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_req, e: RequestValidationError):
        details = [{"at": ".".join(str(x) for x in err["loc"]), "problem": err["msg"]}
                   for err in e.errors()]
        message = "; ".join(f"{d['at']}: {d['problem']}" for d in details[:5])
        return JSONResponse({"error": {"code": "invalid_request", "message": message,
                                       "details": details,
                                       "hint": f"see {p}/llms.txt for request shapes"}},
                            status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_req, e: StarletteHTTPException):
        code = {404: "not_found", 405: "method_not_allowed"}.get(e.status_code, "http_error")
        return JSONResponse({"error": {"code": code, "message": str(e.detail),
                                       "hint": f"GET {p}/api/v1 lists the endpoints"}},
                            status_code=e.status_code)

    # Behind a path (yishuapp.com/life) the tunnel forwards the path unchanged, so serve the
    # prefixed routes; keep the bare ones too, so local agents need not know the prefix.
    # Everything an agent needs is under {prefix}/api/, one path to open to the internet.
    public, api = public_router(), api_router()
    app.include_router(public, prefix=p)
    app.include_router(api, prefix=p + API)
    app.include_router(api, prefix=p + WEB + API, include_in_schema=False)
    if p:
        app.include_router(public, include_in_schema=False)
        app.include_router(api, prefix=API, include_in_schema=False)
        app.include_router(api, prefix=WEB + API, include_in_schema=False)
        app.add_api_route(API + "/openapi.json", lambda: app.openapi(), include_in_schema=False)

    @app.middleware("http")
    async def access_guard(request: Request, call_next):
        # A safety net in case the Cloudflare Access app is ever removed or misconfigured:
        # on the public host only the agent API may be reached without signing in.
        if _public(request) and not request.headers.get(ACCESS_HEADER):
            path = request.url.path
            if not (path == p + API or path.startswith(p + API + "/")):
                return JSONResponse({"error": {
                    "code": "forbidden", "message": "sign in through Cloudflare Access",
                    "hint": f"agents use {p}{API} with a token"}}, status_code=403)
        return await call_next(request)
    return app


def _base(request: Request) -> str:
    """The URL this request came in under, with the prefix only if the request used it."""
    p = request.app.state.settings.prefix
    path = request.url.path
    used = bool(p) and (path == p or path.startswith(p + "/"))
    return str(request.base_url).rstrip("/") + (p if used else "")


def public_router() -> APIRouter:
    r = APIRouter()

    def manual(request: Request) -> PlainTextResponse:
        return PlainTextResponse(MANUAL.replace("{BASE}", _base(request)),
                                 media_type="text/markdown; charset=utf-8")

    r.add_api_route("/llms.txt", manual, methods=["GET"], include_in_schema=False)
    r.add_api_route(API + "/manual", manual, methods=["GET"], summary="Agent manual (Markdown)")

    @r.get("/healthz", include_in_schema=False)
    def healthz():
        return {"ok": True}

    @r.get("/", include_in_schema=False)
    def page():
        return FileResponse(WEB_DIR / "index.html", headers=PAGE_HEADERS)

    @r.get("/static/{name}", include_in_schema=False)
    def static(name: str):
        path = WEB_DIR / name
        if not re.fullmatch(r"[\w-]+\.(js|css|svg|png)", name) or not path.is_file():
            raise not_found("file", name)
        return FileResponse(path, headers=PAGE_HEADERS)

    @r.get(API, summary="Discovery: what this API is and where to start")
    def index(request: Request):
        b = _base(request)
        return {
            "name": "lifeos", "version": __version__,
            "about": "The owner's personal records: meals, workouts, Apple Health, mood, "
                     "diary, body measurements and anything else, stored on their own machine.",
            "api_root": f"{b}{API}", "manual": f"{b}{API}/manual",
            "openapi": f"{b}{API}/openapi.json",
            "auth": "Authorization: Bearer <token> on every /api/v1 call except this one",
            "start_here": f"GET {b}{API}/context",
        }

    return r


def api_router() -> APIRouter:
    r = APIRouter()

    # --- who and what -------------------------------------------------------------------
    @r.get("/whoami", tags=["meta"])
    def whoami(actor: Reader):
        return {"name": actor.name, "scope": actor.scope}

    @r.get("/context", tags=["meta"],
           summary="Everything an agent needs before writing: time, profile, categories, today")
    def context(conn: Db, actor: Reader):
        tz = profile.timezone_of(conn)
        today = now(tz)
        recent = records.search(conn, limit=10)["records"]
        return {
            "now": iso(today), "today": today.date().isoformat(), "timezone": tz.key,
            "you": {"name": actor.name, "scope": actor.scope},
            "profile": profile.get_all(conn),
            "profile_keys_recommended": profile.RECOMMENDED_KEYS,
            "categories": [{"name": c["name"], "title": c["title"],
                            "description": c["description"]}
                           for c in conn.execute(
                               "SELECT * FROM categories ORDER BY builtin DESC, name")],
            "metrics_with_data": [{"name": m["name"], "title": m["title"], "unit": m["unit"],
                                   "last_date": m["last_date"]}
                                  for m in samples.catalog(conn) if m["last_date"]],
            "today_summary": summary.day(conn, today.date().isoformat()),
            "frequent_foods": [
                {"id": f["id"], "name": f["name"], "aliases": f["aliases"],
                 "serving": f["serving"],
                 "kcal_per_serving": (f["per_serving"] or {}).get("kcal"),
                 "kcal_per_100g": (f["per_100g"] or {}).get("kcal")}
                for f in foods.search(conn, None, 30)],
            "recent_records": [{"id": x["id"], "category": x["category"], "start": x["start"],
                                "title": x["title"], "created_by": x["created_by"]}
                               for x in recent],
        }

    # --- categories ---------------------------------------------------------------------
    def _category_out(conn, c: dict) -> dict:
        n = conn.execute("SELECT COUNT(*) FROM records WHERE category = ? AND deleted_at IS NULL",
                         (c["name"],)).fetchone()[0]
        return {"name": c["name"], "title": c["title"], "description": c["description"],
                "builtin": bool(c["builtin"]), "schema": loads(c["schema"]),
                "example": loads(c["example"]), "records": n, "created_by": c["created_by"],
                "updated_at": c["updated_at"]}

    def _check_schema(schema: dict) -> str:
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as e:
            raise bad_request(f"invalid JSON Schema: {e.message}") from None
        if schema.get("type") != "object":
            raise bad_request('schema must describe an object: {"type": "object", ...}')
        return dumps(schema)

    @r.get("/categories", tags=["categories"])
    def list_categories(conn: Db, _: Reader):
        rows = conn.execute("SELECT * FROM categories ORDER BY builtin DESC, name").fetchall()
        return {"categories": [_category_out(conn, dict(c)) for c in rows]}

    @r.get("/categories/{name}", tags=["categories"])
    def get_category(name: str, conn: Db, _: Reader):
        return _category_out(conn, records.category(conn, name))

    @r.post("/categories", tags=["categories"], status_code=201)
    def create_category(body: CategoryIn, conn: Db, actor: Writer):
        if not NAME_RE.match(body.name):
            raise bad_request("name must be snake_case, 2-48 chars, starting with a letter")
        schema = _check_schema(body.data_schema)
        if body.example is not None:
            errors = list(Draft202012Validator(body.data_schema).iter_errors(
                body.example.get("data", body.example)))
            if errors:
                raise bad_request(f"example does not match schema: {errors[0].message}")
        stamp = utc_now()
        with tx(conn):
            if conn.execute("SELECT 1 FROM categories WHERE name = ?", (body.name,)).fetchone():
                raise ApiError(409, "exists", f"category {body.name!r} already exists")
            conn.execute(
                "INSERT INTO categories(name, title, description, schema, example, builtin,"
                " created_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?)",
                (body.name, body.title, body.description, schema,
                 dumps(body.example) if body.example else None, actor.name, stamp, stamp))
            out = _category_out(conn, records.category(conn, body.name))
            log_change(conn, actor.name, "create", "category", body.name, after=out, at=stamp)
        return out

    @r.patch("/categories/{name}", tags=["categories"])
    def patch_category(name: str, body: CategoryPatch, conn: Db, actor: Writer):
        changes = body.model_dump(exclude_unset=True, by_alias=False)
        with tx(conn):
            before = _category_out(conn, records.category(conn, name))
            if before["builtin"] and set(changes) - {"title"}:
                raise ApiError(409, "builtin", f"{name} is built in; only its title can change",
                               "data accepts extra fields beyond the schema, or create your "
                               "own category")
            sets = {"title": changes.get("title"), "description": changes.get("description"),
                    "schema": _check_schema(changes["data_schema"])
                    if changes.get("data_schema") else None,
                    "example": dumps(changes["example"]) if changes.get("example") else None}
            for col, val in sets.items():
                if val is not None:
                    conn.execute(f"UPDATE categories SET {col} = ?, updated_at = ? WHERE name = ?",
                                 (val, utc_now(), name))
            after = _category_out(conn, records.category(conn, name))
            log_change(conn, actor.name, "update", "category", name, before=before, after=after)
        return after

    @r.delete("/categories/{name}", tags=["categories"])
    def delete_category(name: str, conn: Db, actor: Admin):
        with tx(conn):
            c = _category_out(conn, records.category(conn, name))
            if c["builtin"]:
                raise ApiError(409, "builtin", f"{name} is built in")
            if conn.execute("SELECT 1 FROM records WHERE category = ?", (name,)).fetchone():
                raise ApiError(409, "not_empty", f"{name} still has records (including deleted)")
            conn.execute("DELETE FROM categories WHERE name = ?", (name,))
            log_change(conn, actor.name, "delete", "category", name, before=c)
        return {"deleted": name}

    # --- records ------------------------------------------------------------------------
    @r.get("/records", tags=["records"], summary="Search records, newest first")
    def list_records(
        conn: Db, _: Reader,
        category: str | None = Query(None, description="one or more, comma-separated"),
        date: str | None = Query(None, description="local date YYYY-MM-DD"),
        date_from: str | None = Query(None, alias="from",
                                      description="YYYY-MM-DD (local date) or ISO time"),
        date_to: str | None = Query(None, alias="to",
                                    description="YYYY-MM-DD inclusive, or ISO time exclusive"),
        q: str | None = Query(None, description="text in title, note or data"),
        tag: str | None = None, source: str | None = None,
        include_deleted: bool = False,
        limit: int = Query(50, ge=1, le=records.MAX_LIMIT),
        cursor: str | None = None,
        order: str = Query("desc", pattern="^(asc|desc)$"),
    ):
        tz = profile.timezone_of(conn)
        kw: dict = {}
        for key, value in (("from", date_from), ("to", date_to)):
            if value is None:
                continue
            if len(value) == 10:
                kw["date_from" if key == "from" else "date_to"] = value
            else:
                try:
                    kw["ts_from" if key == "from" else "ts_to"] = parse(value, tz).timestamp()
                except ValueError as e:
                    raise bad_request(str(e)) from None
        return records.search(conn, categories=_split(category), date=date, q=q, tag=tag,
                              source=source, include_deleted=include_deleted, limit=limit,
                              cursor=cursor, order=order, **kw)

    def _write_response(rec, warnings, created):
        return JSONResponse({"record": rec, "warnings": warnings, "created": created},
                            status_code=201 if created else 200)

    @r.post("/records", tags=["records"], summary="Create a record (or update by external_id)")
    def create_record(body: RecordIn, conn: Db, actor: Writer):
        rec, warnings, created = records.create(
            conn, body.model_dump(), actor.name, profile.timezone_of(conn))
        return _write_response(rec, warnings, created)

    @r.post("/records/batch", tags=["records"], summary="Create up to 500 records atomically")
    def create_records(body: RecordBatch, conn: Db, actor: Writer):
        tz = profile.timezone_of(conn)
        out = []
        with tx(conn):
            for i, item in enumerate(body.records):
                try:
                    rec, warnings, created = records.upsert(conn, item.model_dump(),
                                                            actor.name, tz)
                except ApiError as e:
                    e.message = f"records[{i}]: {e.message}"
                    raise
                out.append({"record": rec, "warnings": warnings, "created": created})
        return {"results": out}

    @r.get("/records/{record_id}", tags=["records"])
    def get_record(record_id: str, conn: Db, _: Reader):
        return records.get(conn, record_id)

    @r.patch("/records/{record_id}", tags=["records"])
    def patch_record(record_id: str, body: RecordPatch, conn: Db, actor: Writer):
        rec, warnings = records.update(conn, record_id, body.model_dump(exclude_unset=True),
                                       actor.name, profile.timezone_of(conn))
        return {"record": rec, "warnings": warnings}

    @r.delete("/records/{record_id}", tags=["records"], summary="Soft-delete (restorable)")
    def delete_record(record_id: str, conn: Db, actor: Writer):
        return {"record": records.set_deleted(conn, record_id, actor.name, True)}

    @r.post("/records/{record_id}/restore", tags=["records"])
    def restore_record(record_id: str, conn: Db, actor: Writer):
        return {"record": records.set_deleted(conn, record_id, actor.name, False)}

    @r.get("/records/{record_id}/series", tags=["records"],
           summary="Time series of one record: workout heart rate, recovery, cadence, power")
    def record_series(record_id: str, conn: Db, _: Reader,
                      name: str | None = Query(None, description="e.g. heart_rate"),
                      points: bool = Query(True, description="false: names and stats only")):
        rec = records.get(conn, record_id, with_attachments=False)
        return {"record_id": record_id,
                "series": series.for_record(conn, rec, profile.timezone_of(conn), name=name,
                                            with_points=points)}

    @r.post("/records/{record_id}/attachments", tags=["records"], status_code=201,
            summary="Attach a file (raw body, or multipart field 'file')")
    async def add_attachment(record_id: str, request: Request, conn: Db, actor: Writer,
                             filename: str | None = Query(None)):
        ctype = request.headers.get("content-type", "")
        if ctype.startswith("multipart/"):
            form = await request.form()
            upload = form.get("file")
            if upload is None or isinstance(upload, str):
                raise bad_request("multipart upload needs a 'file' field")
            content, name, mime = await upload.read(), upload.filename, upload.content_type
        else:
            content, name, mime = await request.body(), filename, ctype.split(";")[0] or None
        if not content:
            raise bad_request("empty file")
        if len(content) > 50 * 1024 * 1024:
            raise ApiError(413, "too_large", "attachments are limited to 50 MB")
        files_dir = request.app.state.settings.files_dir

        def save():
            with tx(conn):
                return records.add_attachment(conn, files_dir, record_id,
                                              name or "attachment", mime, content, actor.name)
        return await run_in_threadpool(save)

    @r.get("/attachments/{attachment_id}", tags=["records"])
    def get_attachment(attachment_id: str, request: Request, conn: Db, _: Reader):
        a = records.attachment(conn, attachment_id)
        return FileResponse(request.app.state.settings.files_dir / a["path"],
                            media_type=a["mime"], filename=a["filename"])

    # --- samples and metrics ------------------------------------------------------------
    @r.get("/metrics", tags=["samples"], summary="Metric catalog with the dates that have data")
    def list_metrics(conn: Db, _: Reader, counts: bool = False):
        return {"metrics": samples.catalog(conn, with_counts=counts)}

    @r.post("/metrics", tags=["samples"], status_code=201,
            summary="Declare a custom metric (title, unit, how days aggregate)")
    def create_metric(body: MetricIn, conn: Db, actor: Writer):
        if not NAME_RE.match(body.name):
            raise bad_request("name must be snake_case, 2-48 chars, starting with a letter")
        if body.aggregation not in ("sum", "avg", "last"):
            raise bad_request("aggregation must be sum, avg or last")
        with tx(conn):
            if samples.metric(conn, body.name):
                raise ApiError(409, "exists", f"metric {body.name!r} already exists")
            samples.ensure_metric(conn, body.name, body.unit, aggregation=body.aggregation,
                                  title=body.title)
            log_change(conn, actor.name, "create", "metric", body.name, after=body.model_dump())
        return samples.metric(conn, body.name)

    @r.get("/metrics/{name}/series", tags=["samples"],
           summary="Aggregated values per hour/day/week/month")
    def metric_series(name: str, conn: Db, _: Reader,
                      date_from: str = Query(..., alias="from", description="YYYY-MM-DD"),
                      date_to: str = Query(..., alias="to", description="YYYY-MM-DD inclusive"),
                      bucket: str = Query("day", pattern="^(hour|day|week|month)$")):
        for d in (date_from, date_to):
            if len(d) != 10:
                raise bad_request("from and to are dates: YYYY-MM-DD")
        return samples.series(conn, name, date_from, date_to, bucket, profile.timezone_of(conn))

    @r.get("/samples", tags=["samples"], summary="Raw samples, newest first")
    def list_samples(conn: Db, _: Reader, metric: str | None = None,
                     date_from: str | None = Query(None, alias="from"),
                     date_to: str | None = Query(None, alias="to"),
                     source: str | None = None,
                     limit: int = Query(100, ge=1, le=samples.MAX_LIMIT),
                     cursor: str | None = None):
        return samples.search(conn, metric_name=metric, date_from=date_from, date_to=date_to,
                              source=source, limit=limit, cursor=cursor)

    @r.post("/samples", tags=["samples"], summary="Add measurements (weight, water, ...)")
    def add_samples(body: SampleBatch, conn: Db, actor: Writer):
        items = [s.model_dump(exclude_none=True) for s in body.samples]
        return samples.add(conn, items, actor.name, profile.timezone_of(conn))

    @r.delete("/samples/{sample_id}", tags=["samples"])
    def delete_sample(sample_id: int, conn: Db, actor: Writer):
        return {"deleted": samples.delete(conn, sample_id, actor.name)}

    # --- foods --------------------------------------------------------------------------
    @r.get("/foods", tags=["foods"], summary="Search the personal food library")
    def list_foods(conn: Db, _: Reader, q: str | None = None,
                   limit: int = Query(20, ge=1, le=200)):
        return {"foods": foods.search(conn, q, limit)}

    @r.post("/foods", tags=["foods"], status_code=201)
    def create_food(body: FoodIn, conn: Db, actor: Writer):
        return foods.create(conn, body.model_dump(), actor.name)

    @r.get("/foods/{food_id}", tags=["foods"])
    def get_food(food_id: str, conn: Db, _: Reader):
        f = foods.get_food(conn, food_id)
        if f is None:
            raise not_found("food", food_id)
        return f

    @r.patch("/foods/{food_id}", tags=["foods"])
    def patch_food(food_id: str, body: FoodPatch, conn: Db, actor: Writer):
        return foods.update(conn, food_id, body.model_dump(exclude_unset=True), actor.name)

    @r.delete("/foods/{food_id}", tags=["foods"])
    def delete_food(food_id: str, conn: Db, actor: Writer):
        foods.delete(conn, food_id, actor.name)
        return {"deleted": food_id}

    # --- profile ------------------------------------------------------------------------
    @r.get("/profile", tags=["profile"])
    def get_profile(conn: Db, _: Reader):
        return {"profile": profile.get_all(conn), "recommended_keys": profile.RECOMMENDED_KEYS}

    @r.patch("/profile", tags=["profile"], summary="Merge keys; null deletes a key")
    def patch_profile(conn: Db, actor: Writer, body: dict[str, Any] = Body(
            ..., examples=[{"height_cm": 172, "goals": {"kcal": 2000, "protein_g": 110}}])):
        return {"profile": profile.patch(conn, body, actor.name)}

    # --- summaries ----------------------------------------------------------------------
    @r.get("/summary/day", tags=["summary"],
           summary="One day: intake vs goals, activity, rings, sleep, workouts, weight")
    def summary_day(conn: Db, _: Reader, date: str | None = Query(
            None, description="YYYY-MM-DD; default today in the profile timezone")):
        return summary.day(conn, date or now(profile.timezone_of(conn)).date().isoformat())

    @r.get("/summary/days", tags=["summary"], summary="Compact per-day rows for a date range")
    def summary_days(conn: Db, _: Reader, date_from: str = Query(..., alias="from"),
                     date_to: str = Query(..., alias="to")):
        return summary.days(conn, date_from, date_to)

    # --- audit --------------------------------------------------------------------------
    @r.get("/changes", tags=["meta"], summary="Audit log of every write, newest first")
    def list_changes(conn: Db, _: Reader, entity: str | None = None,
                     entity_id: str | None = None, actor: str | None = None,
                     before_id: int | None = None, limit: int = Query(50, ge=1, le=500)):
        where, args = [], []
        for col, val in (("entity", entity), ("entity_id", entity_id), ("actor", actor)):
            if val:
                where.append(f"{col} = ?")
                args.append(val)
        if before_id:
            where.append("id < ?")
            args.append(before_id)
        rows = conn.execute("SELECT * FROM changes" + (" WHERE " + " AND ".join(where)
                                                       if where else "")
                            + " ORDER BY id DESC LIMIT ?", [*args, limit]).fetchall()
        return {"changes": [{**dict(c), "before": loads(c["before"]), "after": loads(c["after"])}
                            for c in rows]}

    # --- imports ------------------------------------------------------------------------
    @r.post("/imports/apple-health", tags=["imports"], status_code=202,
            summary="Upload Apple Health export.zip (raw body or multipart 'file')")
    async def import_apple_health(request: Request, conn: Db, actor: Writer):
        settings = request.app.state.settings
        dest_dir = settings.raw_dir / "apple-health"
        dest_dir.mkdir(parents=True, exist_ok=True)
        stamp = utc_now().replace(":", "").replace("-", "")
        tmp = dest_dir / f".upload-{stamp}"
        size = 0
        try:
            with tmp.open("wb") as out:
                if request.headers.get("content-type", "").startswith("multipart/"):
                    form = await request.form()
                    upload = form.get("file")
                    if upload is None or isinstance(upload, str):
                        raise bad_request("multipart upload needs a 'file' field")
                    while chunk := await upload.read(1 << 20):
                        size += len(chunk)
                        out.write(chunk)
                else:
                    async for chunk in request.stream():
                        size += len(chunk)
                        out.write(chunk)
            if size == 0:
                raise bad_request("empty upload")
            with tmp.open("rb") as f:
                head = f.read(2)
            dest = dest_dir / f"{stamp}-export.{'zip' if head == b'PK' else 'xml'}"
            tmp.rename(dest)
        finally:
            tmp.unlink(missing_ok=True)
        job_id = await run_in_threadpool(request.app.state.jobs.submit_apple_health,
                                         conn, dest, actor.name)
        return {"import_id": job_id, "status": "queued", "bytes": size,
                "poll": f"{_base(request)}{API}/imports/{job_id}"}

    @r.post("/imports/health-auto-export", tags=["imports"],
            summary="Receiver for the Health Auto Export iOS app (REST API automation, JSON)")
    async def import_hae(request: Request, conn: Db, actor: Writer):
        raw = await request.body()
        try:
            payload = json.loads(raw)
        except ValueError:
            raise bad_request("body is not JSON; set the automation's format to JSON") from None
        settings = request.app.state.settings
        stamp = utc_now()
        dest = settings.raw_dir / "health-auto-export" / stamp[:7]
        dest.mkdir(parents=True, exist_ok=True)
        session = re.sub(r"[^\w-]", "", request.headers.get("session-id", ""))[:40]
        path = dest / f"{stamp.replace(':', '')}-{session or 'payload'}.json"
        path.write_bytes(raw)

        def run():
            try:
                stats = health_auto_export.run(conn, payload, actor.name, settings.files_dir)
            except ValueError as e:
                raise bad_request(str(e)) from None
            stats["automation"] = request.headers.get("automation-name")
            job_id = jobs.record(conn, "health_auto_export", str(path), actor.name, "done",
                                 stats)
            log_change(conn, actor.name, "import", "import", job_id, after=stats)
            return {"import_id": job_id, **stats}
        return await run_in_threadpool(run)

    @r.get("/imports", tags=["imports"])
    def list_imports(conn: Db, _: Reader, limit: int = Query(20, ge=1, le=200)):
        return {"imports": jobs.listing(conn, limit)}

    @r.get("/imports/{import_id}", tags=["imports"])
    def get_import(import_id: str, conn: Db, _: Reader):
        j = jobs.get(conn, import_id)
        if j is None:
            raise not_found("import", import_id)
        return j

    # --- export -------------------------------------------------------------------------
    @r.get("/export", tags=["export"],
           summary="Everything except raw samples, as one JSON document")
    def export_all(conn: Db, _: Reader, include_deleted: bool = False):
        recs, cursor = [], None
        while True:
            page = records.search(conn, include_deleted=include_deleted, limit=records.MAX_LIMIT,
                                  cursor=cursor, order="desc")
            recs += page["records"]
            cursor = page["next_cursor"]
            if not cursor:
                break
        return {
            "exported_at": utc_now(), "profile": profile.get_all(conn),
            "categories": [_category_out(conn, dict(c))
                           for c in conn.execute("SELECT * FROM categories")],
            "records": recs, "foods": foods.search(conn, None, 100000),
            "metrics": samples.catalog(conn, with_counts=True),
            "samples": f"GET {API}/export/samples.ndjson",
        }

    @r.get("/export/samples.ndjson", tags=["export"], summary="Every sample, one JSON per line")
    def export_samples(request: Request, _: Reader, metric: str | None = None):
        db_path = request.app.state.settings.db_path

        def rows():
            c = connect(db_path)
            try:
                sql = "SELECT * FROM samples" + (" WHERE metric = ?" if metric else "") + \
                      " ORDER BY id"
                for row in c.execute(sql, (mx.canonical(metric),) if metric else ()):
                    yield dumps(samples._row(row)) + "\n"
            finally:
                c.close()
        return StreamingResponse(rows(), media_type="application/x-ndjson")

    # --- tokens -------------------------------------------------------------------------
    @r.get("/tokens", tags=["admin"])
    def list_tokens(conn: Db, _: Admin):
        return {"tokens": auth.listing(conn)}

    @r.post("/tokens", tags=["admin"], status_code=201,
            summary="Create a token for another agent (shown once)")
    def create_token(body: TokenIn, conn: Db, actor: Admin):
        try:
            token = auth.create(conn, body.name, body.scope)
        except ValueError as e:
            raise bad_request(str(e)) from None
        log_change(conn, actor.name, "create", "token", body.name, after={"scope": body.scope})
        return {"name": body.name, "scope": body.scope, "token": token}

    @r.delete("/tokens/{name}", tags=["admin"])
    def revoke_token(name: str, conn: Db, actor: Admin):
        if not auth.revoke(conn, name):
            raise not_found("active token", name)
        log_change(conn, actor.name, "revoke", "token", name)
        return {"revoked": name}

    return r
