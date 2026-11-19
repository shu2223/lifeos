"""MCP server: lifeos as tools any MCP client can call (Claude, ChatGPT, Cursor, Codex ...).

Streamable HTTP at {prefix}/api/v1/mcp, stateless: each POST carries one JSON-RPC message (or a
batch) and gets a JSON reply. There are no sessions and no server-sent events, which the
transport allows. Every tool wraps one REST endpoint and calls it in-process with the caller's
own token, so validation, scopes, warnings and the audit log are exactly the REST API's.
"""
import asyncio
import json
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urlencode

from fastapi import Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, Response
from jsonschema import Draft202012Validator

from . import __version__, auth
from . import categories as cats
from .db import connect

# Handshake era: the client sends initialize first. Modern era (2026-07-28): no handshake;
# every request carries its version and capabilities in params._meta, mirrored in headers.
PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
MODERN_VERSIONS = ("2026-07-28",)
VERSION_KEY = "io.modelcontextprotocol/protocolVersion"
CAPABILITIES_KEY = "io.modelcontextprotocol/clientCapabilities"
SERVER_INFO_KEY = "io.modelcontextprotocol/serverInfo"
SERVER_INFO = {"name": "lifeos", "title": "lifeos 生活记录", "version": __version__}
CAPABILITIES = {"tools": {"listChanged": False}, "resources": {"listChanged": False}}
CACHEABLE = {"server/discover", "tools/list", "resources/list", "resources/templates/list",
             "resources/read", "prompts/list"}
NAME_PARAM = {"tools/call": "name", "prompts/get": "name", "resources/read": "uri"}
# JSON-RPC error code -> HTTP status, for modern-era replies.
HTTP_STATUS = {-32700: 400, -32600: 400, -32602: 400, -32020: 400, -32021: 400,
               -32022: 400, -32601: 404}
MANUAL_URI = "lifeos://manual"

INSTRUCTIONS = """\
lifeos is the user's own record of their life: meals and calories, drinks and water, workouts, \
weight, sleep, mood, diary, symptoms, medication, spending, cigarettes, plus Apple Health and \
Apple Fitness data that syncs on its own. When the user tells you about their day, write it \
here. When they ask about themselves, read it from here instead of guessing.

- Call get_context once at the start: current time and timezone, profile and goals, \
categories, today so far, frequent foods. Take dates and times from it, not from your own \
sense of the date. Times without an offset are in the user's timezone.
- One message should be enough. "我中午吃了一碗牛肉面加个煎蛋" becomes one log_meal call: \
estimate grams, kcal, protein_g, fat_g and carbs_g for each item, set "estimated": true and a \
"confidence". Ask only when any guess would be wildly off. Never invent events.
- Look up foods the user eats often with search_foods (and frequent_foods in the context) so \
the numbers stay consistent; refer to one with food_id plus amount. Save numbers from a label \
with save_food.
- Read "warnings" in every write result and fix what they name with update_record.
- Fix mistakes with update_record and remove duplicates with delete_record (it can be \
restored). Never write a second record to correct the first.
- Weight and water are measurements: log_measurements with body_mass (kg) or dietary_water \
(mL). Convert cups to mL.
- Cigarettes: log_smoking once per occasion. For "今天一共抽了10根", read smoking.cigarettes \
from day_summary and log only the difference: the user also taps +1 on their web page. Never \
lecture about smoking.
- The user is losing weight. For "还能吃多少" or "吃多了要走多久", read energy in \
day_summary: over_target_kcal (negative means room left), walk_minutes, walk_steps. If logged \
is false, no meals are recorded that day; say so instead of claiming a deficit.
- Workouts, steps, sleep and heart rate arrive from Apple Watch on their own; don't enter them \
again. Add what the user says (RPE, notes) to the existing record with update_record.
- For any other category (mood, journal, medication, symptom, expense, sleep as felt, or one \
you create), use log_record; get_category shows its fields. read_manual has every detail.
- After writing, confirm in one short line in the user's language with the key numbers and \
today's running total, e.g. 记好了：午餐 牛肉面+煎蛋 ≈ 710 kcal，今天累计 1,240 / 2,000 kcal.
- Never reveal the access token or put it into a record.
"""


class RpcError(Exception):
    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(message)
        self.code, self.message, self.data = code, message, data


# --- tools ------------------------------------------------------------------------------

def _s(type_: str, description: str = "", **kw) -> dict:
    out = {"type": type_, **kw}
    if description:
        out["description"] = description
    return out


DATE = _s("string", "local date YYYY-MM-DD")
WHEN = _s("string", "ISO 8601 time, or HH:MM for today; default now. Without an offset it is "
                    "the user's local time")
NOTE = _s("string", "the user's own words, or anything worth keeping")
EXTERNAL_ID = _s("string", "your stable id for this event; sending it again updates the same "
                           "record instead of adding one")
_MEAL = cats.BUILTIN["meal"]["schema"]["properties"]
_SMOKING = cats.BUILTIN["smoking"]["schema"]["properties"]


@dataclass(frozen=True)
class Tool:
    name: str
    title: str
    description: str
    method: str
    path: str  # REST path under /api/v1; {name} parts are filled from arguments
    properties: dict = field(default_factory=dict)
    required: tuple = ()
    read_only: bool = True
    destructive: bool = False
    idempotent: bool = True
    body: Any = None  # arguments -> JSON body; default: the arguments left after the path

    def input_schema(self) -> dict:
        return {"type": "object", "properties": self.properties,
                "required": list(self.required), "additionalProperties": False}

    def describe(self, extra: str = "") -> dict:
        return {
            "name": self.name, "title": self.title,
            "description": self.description + extra,
            "inputSchema": self.input_schema(),
            "annotations": {"title": self.title, "readOnlyHint": self.read_only,
                            "destructiveHint": self.destructive,
                            "idempotentHint": self.idempotent, "openWorldHint": False},
        }


def _record_body(category: str, data_keys: tuple):
    def build(a: dict) -> dict:
        body = {"category": category,
                "data": {k: a[k] for k in data_keys if k in a}}
        for k in ("start", "end", "title", "note", "tags", "external_id"):
            if k in a:
                body[k] = a[k]
        return body
    return build


TOOLS = [
    Tool("get_context", "Start here",
         "Everything to know before writing: current time and timezone, the user's profile and "
         "goals, categories, today's summary, frequent foods and recent records. Call once at "
         "the start of a conversation.", "GET", "/context"),
    Tool("read_manual", "Read the full manual",
         "The full lifeos manual (Markdown): every category's fields, how meals are totalled, "
         "how days are counted, recipes. Read it when a rule or field is unclear.",
         "GET", "/manual"),
    Tool("day_summary", "One day's summary",
         "One day: food intake against goals (kcal, protein, fat, carbs), steps, activity "
         "rings, sleep, workouts, weight, smoking.cigarettes, and the energy balance (energy: "
         "over_target_kcal, walk_minutes, walk_steps, logged).", "GET", "/summary/day",
         {"date": _s("string", "YYYY-MM-DD; default today")}),
    Tool("days_summary", "Day-by-day summary",
         "Compact rows per day for a date range: kcal eaten, steps, sleep, exercise, weight, "
         "cigarettes ... For trends and 'this week / this month' questions.",
         "GET", "/summary/days", {"from": DATE, "to": _s("string", "YYYY-MM-DD inclusive")},
         ("from", "to")),
    Tool("search_records", "Search records",
         "Find records (meals, workouts, mood, journal, smoking ...), newest first. Filter by "
         "category, a date or date range, or text.", "GET", "/records",
         {"category": _s("string", "one or more, comma-separated, e.g. meal or mood,journal"),
          "date": DATE,
          "from": _s("string", "YYYY-MM-DD or ISO time"),
          "to": _s("string", "YYYY-MM-DD inclusive, or ISO time exclusive"),
          "q": _s("string", "text in title, note or data"),
          "include_deleted": _s("boolean"),
          "limit": _s("integer", "default 50", minimum=1, maximum=500),
          "cursor": _s("string", "next_cursor from the previous page")}),
    Tool("get_record", "Get a record", "One record with all its data.",
         "GET", "/records/{id}", {"id": _s("string", "record id, rec_...")}, ("id",)),
    Tool("workout_series", "Workout curves",
         "A workout's heart-rate curve (including two minutes of recovery), per-minute energy "
         "and distance, cadence, power. points=false returns names and stats only.",
         "GET", "/records/{id}/series",
         {"id": _s("string", "workout record id"),
          "name": _s("string", "one series, e.g. heart_rate"),
          "points": _s("boolean", "default true")}, ("id",)),
    Tool("metric_series", "Metric over time",
         "A health metric aggregated per hour, day, week or month: step_count, body_mass, "
         "heart_rate, resting_heart_rate, heart_rate_variability, active_energy, "
         "dietary_water, sleep_* ... get_context lists the metrics that have data.",
         "GET", "/metrics/{name}/series",
         {"name": _s("string", "metric name"), "from": DATE,
          "to": _s("string", "YYYY-MM-DD inclusive"),
          "bucket": _s("string", "default day", enum=["hour", "day", "week", "month"])},
         ("name", "from", "to")),
    Tool("list_samples", "Raw measurements",
         "Raw measurement samples, newest first, e.g. every weigh-in.", "GET", "/samples",
         {"metric": _s("string"), "from": DATE, "to": DATE,
          "limit": _s("integer", "default 100", minimum=1, maximum=1000)}),
    Tool("search_foods", "Search the food library",
         "The user's own food library with nutrients per serving or per 100 g. Check it before "
         "estimating a food the user eats often.", "GET", "/foods",
         {"q": _s("string", "name or alias"), "limit": _s("integer", minimum=1, maximum=200)}),
    Tool("get_category", "Category fields",
         "A category's JSON Schema for data, an example, and its record count.",
         "GET", "/categories/{name}", {"name": _s("string", "e.g. mood")}, ("name",)),
    Tool("get_profile", "Profile and goals",
         "Height, birthday, sex, timezone, daily goals (kcal, protein_g, steps, sleep_hours, "
         "weight_kg, cigarettes_max), diet notes.", "GET", "/profile"),
    Tool("list_changes", "Change history",
         "Who changed what and when, with before and after, newest first.", "GET", "/changes",
         {"entity": _s("string", "record, food, sample, profile, category ..."),
          "entity_id": _s("string"), "actor": _s("string", "token name"),
          "limit": _s("integer", minimum=1, maximum=500)}),

    Tool("log_meal", "Log a meal",
         "Record something eaten or drunk: one call per meal or snack, one item per food, with "
         "nutrients for the amount actually eaten. The server sums the totals, fills meal_type "
         "from the time and writes the title. Returns the record, warnings, and today's "
         "intake.", "POST", "/records",
         {"start": WHEN, "meal_type": _MEAL["meal_type"], "items": _MEAL["items"],
          "place": _MEAL["place"], "estimation_note": _MEAL["estimation_note"],
          "title": _s("string", "generated if omitted"), "note": NOTE,
          "external_id": EXTERNAL_ID},
         ("items",), read_only=False, idempotent=False,
         body=_record_body("meal", ("meal_type", "items", "place", "estimation_note"))),
    Tool("log_smoking", "Log cigarettes",
         "Record cigarettes smoked: once per occasion, count 1 for 'just had one'.",
         "POST", "/records",
         {"count": {**_SMOKING["count"], "default": 1}, "start": WHEN,
          "kind": _SMOKING["kind"], "brand": _SMOKING["brand"],
          "trigger": _SMOKING["trigger"], "note": NOTE},
         read_only=False, idempotent=False,
         body=lambda a: _record_body("smoking", ("count", "kind", "brand", "trigger"))(
             {"count": 1, **a})),
    Tool("log_measurements", "Log measurements",
         "Record numbers: weight body_mass (kg), water dietary_water (mL), body_fat_percentage, "
         "waist_circumference (cm), blood_pressure_systolic ... Unknown metric names are "
         "created. A unit the server cannot convert (like 杯) is refused: give mL.",
         "POST", "/samples",
         {"samples": {"type": "array", "minItems": 1, "items": {
             "type": "object", "required": ["metric", "value"], "additionalProperties": False,
             "properties": {"metric": _s("string", "e.g. body_mass"), "value": _s("number"),
                            "unit": _s("string", "default: the metric's unit"),
                            "start": WHEN, "end": _s("string", "ISO 8601")}}}},
         ("samples",), read_only=False, idempotent=False),
    Tool("log_record", "Log any other record",
         "Record an event in any category: workout (that the watch did not record), mood, "
         "journal, medication, symptom, expense, sleep (as felt), or a custom one. data follows "
         "the category's schema (get_category).", "POST", "/records",
         {"category": _s("string"), "data": _s("object", "fields per the category's schema"),
          "start": WHEN, "end": _s("string", "when it ended, for things with a duration"),
          "title": _s("string", "generated if omitted"), "note": NOTE,
          "tags": _s("array", items={"type": "string"}), "external_id": EXTERNAL_ID},
         ("category",), read_only=False, idempotent=False),
    Tool("update_record", "Fix a record",
         "Change a record. data is a JSON merge patch: objects merge, null deletes a key, "
         "arrays (like meal items) replace, so send the full items list. Use this instead of "
         "writing a second record.", "PATCH", "/records/{id}",
         {"id": _s("string"), "data": _s("object", "merge patch for data"),
          "start": _s("string"), "end": _s("string"), "title": _s("string"),
          "note": _s("string"), "tags": _s("array", items={"type": "string"}),
          "category": _s("string")},
         ("id",), read_only=False, destructive=True),
    Tool("delete_record", "Delete a record",
         "Delete a record, e.g. a duplicate. It can be restored with restore_record.",
         "DELETE", "/records/{id}", {"id": _s("string")}, ("id",),
         read_only=False, destructive=True),
    Tool("restore_record", "Restore a record", "Undo delete_record.",
         "POST", "/records/{id}/restore", {"id": _s("string")}, ("id",), read_only=False),
    Tool("delete_measurement", "Delete a measurement",
         "Delete one sample (id from list_samples), e.g. a mistyped weight.",
         "DELETE", "/samples/{id}", {"id": _s("integer")}, ("id",),
         read_only=False, destructive=True),
    Tool("save_food", "Save a food",
         "Add a food to the library, e.g. from a nutrition label, so later meals reuse its "
         "numbers. Nutrient keys: kcal, protein_g, fat_g, carbs_g, fiber_g, sugar_g, sodium_mg.",
         "POST", "/foods",
         {"name": _s("string"), "aliases": _s("array", items={"type": "string"}),
          "brand": _s("string"),
          "serving": _s("object", "{amount, unit, grams?}, e.g. {amount: 1, unit: 碗, grams: "
                                  "550}"),
          "per_serving": _s("object", "nutrients per serving",
                            additionalProperties={"type": "number"}),
          "per_100g": _s("object", "nutrients per 100 g", additionalProperties={"type": "number"}),
          "note": _s("string"),
          "source": _s("string", enum=["estimate", "label", "user", "database"])},
         ("name",), read_only=False, idempotent=False),
    Tool("update_food", "Fix a food", "Change a food in the library.",
         "PATCH", "/foods/{id}",
         {"id": _s("string"), "name": _s("string"),
          "aliases": _s("array", items={"type": "string"}), "brand": _s("string"),
          "serving": _s("object"), "per_serving": _s("object"), "per_100g": _s("object"),
          "note": _s("string"), "source": _s("string")},
         ("id",), read_only=False, destructive=True),
    Tool("update_profile", "Update profile or goals",
         "Merge keys into the profile; null deletes a key; goals merge one level. E.g. "
         "{\"goals\": {\"kcal\": 1800}} or {\"height_cm\": 172}.", "PATCH", "/profile",
         {"changes": _s("object", "keys to merge")}, ("changes",), read_only=False,
         destructive=True, body=lambda a: a["changes"]),
    Tool("create_category", "Create a category",
         "Add a category for something new to track (reading, skincare, study time ...), "
         "with a JSON Schema for its data. Check get_context's categories first.",
         "POST", "/categories",
         {"name": _s("string", "snake_case, 2-48 chars"),
          "title": _s("string", "display name, e.g. 读书"),
          "description": _s("string", "what belongs here, written for other agents"),
          "schema": _s("object", "JSON Schema (2020-12) of data, type object"),
          "example": _s("object")},
         ("name", "title", "description"), read_only=False, idempotent=False),
]
BY_NAME = {t.name: t for t in TOOLS}
VALIDATORS = {t.name: Draft202012Validator(t.input_schema()) for t in TOOLS}


def _log_record_extra(categories: list[dict]) -> str:
    if not categories:
        return ""
    return " Categories: " + ", ".join(f"{c['name']} ({c['title']})" for c in categories) + "."


# --- calling the REST API in-process ----------------------------------------------------

async def _call(request: Request, token: str, method: str, path: str,
                query: dict | None = None, body: Any = None) -> tuple[int, Any]:
    app = request.app
    qs = urlencode({k: str(v).lower() if isinstance(v, bool) else v
                    for k, v in (query or {}).items() if v is not None})
    payload = b"" if body is None else json.dumps(body, ensure_ascii=False).encode()
    headers = [(b"host", (request.headers.get("host") or "localhost").encode("latin-1")),
               (b"authorization", f"Bearer {token}".encode("latin-1")),
               (b"accept", b"application/json")]
    if body is not None:
        headers += [(b"content-type", b"application/json"),
                    (b"content-length", str(len(payload)).encode())]
    full = app.state.settings.prefix + "/api/v1" + path
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
             "method": method, "scheme": request.url.scheme, "path": full,
             "raw_path": full.encode(), "root_path": "", "query_string": qs.encode(),
             "headers": headers, "client": ("127.0.0.1", 0),
             "server": (request.url.hostname or "localhost", request.url.port or 80)}
    sent = False
    done = asyncio.Event()
    status, chunks, ctype = 500, [], ""

    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": payload, "more_body": False}
        await done.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        nonlocal status, ctype
        if message["type"] == "http.response.start":
            status = message["status"]
            for k, v in message.get("headers", []):
                if k.lower() == b"content-type":
                    ctype = v.decode("latin-1")
        elif message["type"] == "http.response.body":
            chunks.append(message.get("body", b""))
            if not message.get("more_body"):
                done.set()

    await app(scope, receive, send)
    done.set()
    raw = b"".join(chunks).decode("utf-8", "replace")
    if "json" in ctype:
        try:
            return status, json.loads(raw)
        except ValueError:
            pass
    return status, raw


def _text(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


async def call_tool(request: Request, token: str, name: str, args: dict) -> dict:
    tool = BY_NAME.get(name)
    if tool is None:
        raise RpcError(-32602, f"unknown tool: {name}")
    errors = sorted(VALIDATORS[name].iter_errors(args), key=lambda e: list(e.path))
    if errors:
        msg = "; ".join(f"{'.'.join(str(p) for p in e.path) or 'arguments'}: {e.message}"
                        for e in errors[:5])
        return {"content": [{"type": "text", "text": f"invalid arguments: {msg}"}],
                "isError": True}
    rest = dict(args)
    path = tool.path
    for key in [k for k in rest if "{" + k + "}" in path]:
        path = path.replace("{" + key + "}", quote(str(rest.pop(key)), safe=""))
    if tool.method in ("GET", "DELETE"):
        status, data = await _call(request, token, tool.method, path, query=rest)
    else:
        body = tool.body(rest) if tool.body else rest
        status, data = await _call(request, token, tool.method, path, body=body)
    if status >= 400:
        err = data.get("error", data) if isinstance(data, dict) else {"message": data}
        return {"content": [{"type": "text", "text": _text({"status": status, **err})}],
                "isError": True}
    return {"content": [{"type": "text", "text": _text(data)}], "isError": False}


# --- JSON-RPC ---------------------------------------------------------------------------

def _categories(settings) -> list[dict]:
    conn = connect(settings.db_path)
    try:
        return [{"name": r["name"], "title": r["title"]} for r in conn.execute(
            "SELECT name, title FROM categories ORDER BY builtin DESC, name")]
    finally:
        conn.close()


def _modern_version(msg: dict, headers) -> str | None:
    """The 2026-07-28-era version a request asks for, or None for a handshake-era request.
    Raises RpcError when the envelope and its headers disagree or the version is unknown."""
    params = msg.get("params")
    meta = params.get("_meta") if isinstance(params, dict) else None
    if not (isinstance(meta, dict) and VERSION_KEY in meta) and \
            msg.get("method") != "server/discover":
        return None
    meta = meta if isinstance(meta, dict) else {}
    version = meta.get(VERSION_KEY)
    if headers is not None:
        if headers.get("mcp-protocol-version") != version:
            raise RpcError(-32020, "MCP-Protocol-Version header does not match "
                                   "params._meta's protocol version")
        if headers.get("mcp-method") not in (None, msg.get("method")):
            raise RpcError(-32020, "Mcp-Method header does not match the method")
        key = NAME_PARAM.get(msg.get("method"))
        if key and headers.get("mcp-name") not in (None, params.get(key)):
            raise RpcError(-32020, "Mcp-Name header does not match the request")
    if not isinstance(version, str) or CAPABILITIES_KEY not in meta:
        raise RpcError(-32602, f"params._meta needs {VERSION_KEY!r} and {CAPABILITIES_KEY!r}")
    if version not in MODERN_VERSIONS:
        raise RpcError(-32022, f"unsupported protocol version {version}",
                       {"supported": [*MODERN_VERSIONS, *PROTOCOL_VERSIONS],
                        "requested": version})
    return version


async def handle(request: Request, token: str, msg: Any, headers=None) -> dict | None:
    """Answer one JSON-RPC message; None for notifications and stray responses. `headers`
    are checked against a modern-era envelope (single-message POSTs only)."""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
        return {"jsonrpc": "2.0", "id": None,
                "error": {"code": -32600, "message": "not a JSON-RPC 2.0 message"}}
    if "method" not in msg:  # a response to a server request; we never send any
        return None
    method, mid = msg["method"], msg.get("id")
    params = msg.get("params") or {}
    try:
        modern = _modern_version(msg, headers)
        if method == "initialize":
            asked = params.get("protocolVersion")
            result = {
                "protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
                "capabilities": CAPABILITIES, "serverInfo": SERVER_INFO,
                "instructions": INSTRUCTIONS,
            }
        elif method == "server/discover":
            result = {"supportedVersions": [*MODERN_VERSIONS, *PROTOCOL_VERSIONS],
                      "capabilities": CAPABILITIES, "instructions": INSTRUCTIONS}
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            cats_now = await run_in_threadpool(_categories, request.app.state.settings)
            result = {"tools": [t.describe(_log_record_extra(cats_now)
                                           if t.name == "log_record" else "") for t in TOOLS]}
        elif method == "tools/call":
            args = params.get("arguments") or {}
            if not isinstance(args, dict):
                raise RpcError(-32602, "arguments must be an object")
            result = await call_tool(request, token, params.get("name", ""), args)
        elif method == "resources/list":
            result = {"resources": [{"uri": MANUAL_URI, "name": "manual",
                                     "title": "lifeos manual", "mimeType": "text/markdown",
                                     "description": "Every endpoint, category and rule"}]}
        elif method == "resources/templates/list":
            result = {"resourceTemplates": []}
        elif method == "resources/read":
            if params.get("uri") != MANUAL_URI:
                raise RpcError(-32002, f"unknown resource: {params.get('uri')}")
            status, text = await _call(request, token, "GET", "/manual")
            result = {"contents": [{"uri": MANUAL_URI, "mimeType": "text/markdown",
                                    "text": _text(text)}]}
        elif method == "prompts/list":
            result = {"prompts": []}
        elif method.startswith("notifications/"):
            return None
        else:
            raise RpcError(-32601, f"method not found: {method}")
    except RpcError as e:
        if "id" not in msg:
            return None
        err = {"code": e.code, "message": e.message}
        if e.data is not None:
            err["data"] = e.data
        return {"jsonrpc": "2.0", "id": mid, "error": err}
    if "id" not in msg:
        return None
    if modern:
        result = {**result, "resultType": "complete",
                  "_meta": {**result.get("_meta", {}), SERVER_INFO_KEY: SERVER_INFO}}
        if method in CACHEABLE:
            result |= {"ttlMs": 0, "cacheScope": "private"}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


# --- HTTP -------------------------------------------------------------------------------

def _token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    if header[:7].lower() == "bearer ":
        return header[7:].strip() or None
    return request.headers.get("x-api-key")


def _verify(settings, token: str) -> auth.Actor | None:
    conn = connect(settings.db_path)
    try:
        return auth.verify(conn, token)
    finally:
        conn.close()


async def endpoint(request: Request, api_root: str) -> Response:
    if request.method != "POST":
        return Response(status_code=405, headers={"Allow": "POST"})
    token = _token(request)
    actor = token and await run_in_threadpool(_verify, request.app.state.settings, token)
    if not actor:
        challenge = (f'Bearer realm="lifeos", resource_metadata="{api_root}'
                     f'/.well-known/oauth-protected-resource"')
        if token:
            challenge += ', error="invalid_token"'
        return JSONResponse(
            {"error": {"code": "unauthorized",
                       "message": "unknown or revoked token" if token else "missing token",
                       "hint": "send 'Authorization: Bearer <token>', or connect with OAuth"}},
            status_code=401, headers={"WWW-Authenticate": challenge})
    try:
        body = json.loads(await request.body())
    except ValueError:
        return JSONResponse({"jsonrpc": "2.0", "id": None,
                             "error": {"code": -32700, "message": "parse error"}},
                            status_code=400)
    if isinstance(body, list):
        out = [r for r in [await handle(request, token, m) for m in body] if r is not None]
    else:
        out = await handle(request, token, body, request.headers)
    if not out:
        return Response(status_code=202)
    status = 200
    version = request.headers.get("mcp-protocol-version")
    if isinstance(out, dict) and "error" in out and version and version not in PROTOCOL_VERSIONS:
        status = HTTP_STATUS.get(out["error"]["code"], 200)
    return JSONResponse(out, status_code=status)
