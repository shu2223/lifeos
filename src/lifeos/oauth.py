"""OAuth 2.1 for MCP clients that cannot take a pasted token (Claude's apps, ChatGPT ...).

The flow the MCP spec asks for: protected-resource metadata points at this authorization
server; the client registers itself (dynamic client registration), sends the owner's browser
to the consent page with PKCE, and swaps the code for a token.

The consent page lives outside {prefix}/api/, so on the public host Cloudflare Access signs the
owner in before it loads: no one else can approve a client. Each approval mints an ordinary
lifeos token named after the client, which appears in the web page's token list and can be
revoked there like any other.
"""
import base64
import hashlib
import html
import re
import secrets
import time
from urllib.parse import urlencode, urlsplit

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from . import auth
from .db import connect, dumps, loads, log_change, new_id, tx
from .timeutil import utc_now

API = "/api/v1"
CODE_TTL = 600
MAX_CLIENTS = 500
SCOPES = ("read", "write")
ACCESS_HEADER = "cf-access-jwt-assertion"
NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}
PAGE_HEADERS = {
    # No form-action: approving redirects to the client's own (often custom-scheme) callback.
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; "
                               "frame-ancestors 'none'; base-uri 'none'",
    # same-origin, not no-referrer: under no-referrer Safari posts the form with Origin: null.
    "X-Content-Type-Options": "nosniff", "Referrer-Policy": "same-origin",
    "Cache-Control": "no-store",
}
LOOPBACK = ("localhost", "127.0.0.1", "[::1]", "::1")


FORM_TTL = 900
_forms: dict[str, tuple[str, float]] = {}  # consent-form token -> (request fingerprint, expiry)


def _fingerprint(q: dict) -> str:
    return _hash("\n".join(q.get(k) or "" for k in AUTH_PARAMS))


def _form_token(q: dict) -> str:
    """A one-time token in the consent form: a POST without it did not come from the page this
    person was shown, whatever its Origin says."""
    now = time.time()
    for k in [k for k, (_, exp) in _forms.items() if exp < now]:
        del _forms[k]
    token = secrets.token_urlsafe(24)
    _forms[token] = (_fingerprint(q), now + FORM_TTL)
    return token


def _take_form_token(token: str | None, q: dict) -> bool:
    entry = _forms.pop(token or "", None)
    return bool(entry) and entry[1] >= time.time() and secrets.compare_digest(
        entry[0], _fingerprint(q))


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _db(request: Request):
    return connect(request.app.state.settings.db_path)


def _origin(request: Request) -> str:
    return str(request.base_url).rstrip("/")


def site_of(request: Request) -> str:
    """https://host{prefix} as this request reached us (the prefix only if the path used it)."""
    p = request.app.state.settings.prefix
    path = request.url.path
    used = bool(p) and (path == p or path.startswith(p + "/"))
    return _origin(request) + (p if used else "")


# --- metadata ---------------------------------------------------------------------------

def resource_metadata(site: str) -> dict:
    return {"resource": f"{site}{API}/mcp", "authorization_servers": [f"{site}{API}"],
            "scopes_supported": list(SCOPES), "bearer_methods_supported": ["header"],
            "resource_name": "lifeos", "resource_documentation": f"{site}{API}/manual"}


def server_metadata(site: str) -> dict:
    issuer = f"{site}{API}"
    return {
        "issuer": issuer,
        "authorization_endpoint": f"{site}/oauth/authorize",
        "token_endpoint": f"{issuer}/oauth/token",
        "registration_endpoint": f"{issuer}/oauth/register",
        "revocation_endpoint": f"{issuer}/oauth/revoke",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none", "client_secret_post",
                                                  "client_secret_basic"],
        "revocation_endpoint_auth_methods_supported": ["none", "client_secret_post",
                                                       "client_secret_basic"],
        "scopes_supported": list(SCOPES),
        "authorization_response_iss_parameter_supported": True,
        "service_documentation": f"{site}{API}/manual",
    }


def _oauth_error(error: str, description: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": error, "error_description": description},
                        status_code=status, headers=NO_STORE)


# --- clients ----------------------------------------------------------------------------

def _redirect_ok(uri: str) -> bool:
    try:
        u = urlsplit(uri)
    except ValueError:
        return False
    if u.fragment or not u.scheme:
        return False
    if u.scheme == "https":
        return bool(u.hostname)
    if u.scheme == "http":
        return u.hostname in LOOPBACK
    # Private-use schemes of native apps (cursor://, vscode://, com.example.app:/cb).
    return bool(re.fullmatch(r"[a-z][a-z0-9+.-]*", u.scheme)) and u.scheme not in (
        "javascript", "data", "file", "vbscript", "about", "blob")


def _redirect_matches(registered: list[str], uri: str) -> bool:
    if uri in registered:
        return True
    # RFC 8252 7.3: a loopback redirect may use any port.
    u = urlsplit(uri)
    if u.scheme != "http" or u.hostname not in LOOPBACK:
        return False
    for r in registered:
        v = urlsplit(r)
        if (v.scheme, v.hostname, v.path, v.query) == (u.scheme, u.hostname, u.path, u.query):
            return True
    return False


def _client(conn, client_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM oauth_clients WHERE client_id = ?", (client_id,)).fetchone()
    if row is None:
        return None
    return {**dict(row), "redirect_uris": loads(row["redirect_uris"])}


def _authenticate_client(conn, request: Request, form: dict) -> dict | JSONResponse:
    client_id, secret = form.get("client_id"), form.get("client_secret")
    header = request.headers.get("authorization", "")
    if header[:6].lower() == "basic ":
        try:
            raw = base64.b64decode(header[6:]).decode()
            client_id, _, secret = raw.partition(":")
        except ValueError:
            return _oauth_error("invalid_client", "bad Basic credentials", 401)
    client = _client(conn, client_id or "")
    if client is None:
        return _oauth_error("invalid_client", "unknown client_id", 401)
    if client["secret_hash"] and not (secret and secrets.compare_digest(
            client["secret_hash"], _hash(secret))):
        return _oauth_error("invalid_client", "wrong or missing client_secret", 401)
    return client


def _token_name(conn, client_name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", client_name.lower()).strip("-")[:24].strip("-")
    slug = slug or "mcp-client"
    while True:
        name = f"{slug}-{secrets.token_hex(2)}"
        if not conn.execute("SELECT 1 FROM tokens WHERE name = ?", (name,)).fetchone():
            return name


def _issue(conn, client: dict, scope: str, replace: str | None = None) -> dict:
    """Mint a lifeos token for the client, plus a refresh token. Called inside a transaction."""
    if replace:
        name = replace
        conn.execute("UPDATE tokens SET revoked_at = ? WHERE name = ? AND revoked_at IS NULL",
                     (utc_now(), name))
    else:
        # A client that signs in again replaces its earlier token instead of piling them up.
        for g in conn.execute("SELECT token_name FROM oauth_grants WHERE client_id = ?",
                              (client["client_id"],)).fetchall():
            conn.execute("UPDATE tokens SET revoked_at = ? WHERE name = ? AND revoked_at IS NULL",
                         (utc_now(), g["token_name"]))
        conn.execute("DELETE FROM oauth_grants WHERE client_id = ?", (client["client_id"],))
        name = _token_name(conn, client["client_name"])
    token = auth.PREFIX + secrets.token_urlsafe(32)
    conn.execute("DELETE FROM tokens WHERE name = ? AND revoked_at IS NOT NULL", (name,))
    conn.execute("INSERT INTO tokens(id, name, token_hash, scope, created_at)"
                 " VALUES (?, ?, ?, ?, ?)",
                 (new_id("tok"), name, auth._hash(token), scope, utc_now()))
    refresh = "lifeos_rt_" + secrets.token_urlsafe(32)
    conn.execute("DELETE FROM oauth_grants WHERE token_name = ?", (name,))
    conn.execute("INSERT INTO oauth_grants(refresh_hash, client_id, token_name, scope,"
                 " created_at) VALUES (?, ?, ?, ?, ?)",
                 (_hash(refresh), client["client_id"], name, scope, utc_now()))
    if not replace:
        log_change(conn, "oauth", "create", "token", name,
                   after={"scope": scope, "client": client["client_name"]})
    return {"access_token": token, "token_type": "Bearer", "scope": scope,
            "refresh_token": refresh}


# --- consent page -----------------------------------------------------------------------

def _owner_ok(request: Request) -> str | None:
    """None if the person at this page may approve; otherwise why not."""
    s = request.app.state.settings
    if not s.public_host:
        return None  # a local or test install without a public site
    if request.headers.get("host") != s.public_host:
        return f"请从 https://{s.public_host} 打开授权页。"
    if not request.headers.get(ACCESS_HEADER):
        return "没有通过 Cloudflare Access 登录。"
    return None


def _page(title: str, body: str, status: int = 200) -> HTMLResponse:
    doc = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark"><title>{html.escape(title)} · lifeos</title>
<style>
body{{font:16px/1.5 -apple-system,BlinkMacSystemFont,"PingFang SC","Helvetica Neue",sans-serif;
margin:0;min-height:100vh;display:grid;place-items:center;background:#f2f2f7;color:#1c1c1e}}
main{{background:#fff;border-radius:18px;padding:28px 24px;max-width:380px;width:calc(100% - 32px);
box-sizing:border-box;box-shadow:0 1px 3px rgba(0,0,0,.08)}}
h1{{font-size:22px;margin:0 0 12px}} p{{margin:8px 0;color:#3a3a3c}} .muted{{color:#8e8e93;font-size:14px}}
code{{font-size:14px;word-break:break-all}} label{{display:block;margin:16px 0 4px;font-weight:600}}
select,button{{font:inherit;width:100%;padding:12px;border-radius:12px;border:1px solid #d1d1d6;box-sizing:border-box}}
.row{{display:flex;gap:12px;margin-top:20px}} button{{border:0;font-weight:600;cursor:pointer}}
.ok{{background:#007aff;color:#fff}} .no{{background:#e5e5ea;color:#1c1c1e}}
@media (prefers-color-scheme:dark){{body{{background:#000;color:#f2f2f7}} main{{background:#1c1c1e}}
p{{color:#d1d1d6}} select{{background:#2c2c2e;color:#f2f2f7;border-color:#3a3a3c}} .no{{background:#3a3a3c;color:#f2f2f7}}}}
</style></head><body><main>{body}</main></body></html>"""
    return HTMLResponse(doc, status_code=status, headers=PAGE_HEADERS)


def _error_page(message: str, status: int = 400) -> HTMLResponse:
    return _page("无法授权", f"<h1>无法授权</h1><p>{html.escape(message)}</p>", status)


def _redirect(uri: str, params: dict) -> RedirectResponse:
    sep = "&" if urlsplit(uri).query else "?"
    return RedirectResponse(uri + sep + urlencode({k: v for k, v in params.items()
                                                   if v is not None}),
                            status_code=303, headers={"Cache-Control": "no-store"})


AUTH_PARAMS = ("response_type", "client_id", "redirect_uri", "code_challenge",
               "code_challenge_method", "state", "scope", "resource")


def _check_request(conn, q: dict) -> tuple[dict | None, HTMLResponse | RedirectResponse | None]:
    client = _client(conn, q.get("client_id") or "")
    if client is None:
        return None, _error_page("这个应用没有注册（client_id 不认识）。请在应用里重新连接。")
    uri = q.get("redirect_uri") or (client["redirect_uris"][0]
                                    if len(client["redirect_uris"]) == 1 else "")
    if not _redirect_matches(client["redirect_uris"], uri):
        return None, _error_page("回调地址和这个应用注册的不一致，已拒绝。")
    q["redirect_uri"] = uri
    fail = None
    if q.get("response_type") != "code":
        fail = ("unsupported_response_type", "only response_type=code")
    elif not q.get("code_challenge") or q.get("code_challenge_method") != "S256":
        fail = ("invalid_request", "PKCE with code_challenge_method=S256 is required")
    if fail:
        return None, _redirect(uri, {"error": fail[0], "error_description": fail[1],
                                     "state": q.get("state")})
    return client, None


def _scope_of(requested: str | None) -> str:
    asked = set((requested or "").split())
    return "read" if asked and "write" not in asked and "read" in asked else "write"


async def authorize_page(request: Request) -> Response:
    why = _owner_ok(request)
    if why:
        return _error_page(why, 403)
    q = {k: request.query_params.get(k) for k in AUTH_PARAMS}
    conn = _db(request)
    try:
        client, problem = _check_request(conn, q)
    finally:
        conn.close()
    if problem:
        return problem
    scope = _scope_of(q.get("scope"))
    target = urlsplit(q["redirect_uri"])
    where = target.hostname or target.scheme + "://"
    hidden = "".join(f'<input type="hidden" name="{k}" value="{html.escape(v)}">'
                     for k, v in q.items() if v)
    hidden += f'<input type="hidden" name="form_token" value="{_form_token(q)}">'
    options = "".join(f'<option value="{v}"{" selected" if v == scope else ""}>{label}</option>'
                      for v, label in (("write", "读写：可以记录和修改"), ("read", "只读")))
    body = f"""<h1>允许「{html.escape(client['client_name'])}」使用 lifeos？</h1>
<p>它将能读取你的饮食、运动、健康数据和日记{"，并替你记录和修改" if scope == "write" else ""}。</p>
<p class="muted">授权后会回到 <code>{html.escape(where)}</code>。不认识这个地址就拒绝。</p>
<form method="post">{hidden}
<label for="scope">权限</label><select id="scope" name="grant_scope">{options}</select>
<div class="row"><button class="no" name="decision" value="deny">拒绝</button>
<button class="ok" name="decision" value="allow">允许</button></div></form>
<p class="muted">之后可以在 lifeos 的“设置 → 接入的 agent”里随时吊销。</p>"""
    return _page("授权", body)


async def authorize_submit(request: Request) -> Response:
    why = _owner_ok(request)
    if why:
        return _error_page(why, 403)
    s = request.app.state.settings
    origin = request.headers.get("origin")
    expected = f"https://{s.public_host}" if s.public_host else _origin(request)
    # Browsers may send Origin: null (privacy settings, in-app browsers); the form token is
    # what proves the page; a real other origin is refused outright.
    if origin not in (None, "null", expected):
        return _error_page("请求不是从授权页发出的，已拒绝。", 403)
    form = dict(await request.form())
    q = {k: form.get(k) for k in AUTH_PARAMS}
    if not _take_form_token(form.get("form_token"), q):
        return _error_page("授权页已过期或已经用过，请回到应用里重新连接。", 403)

    def work():
        conn = _db(request)
        try:
            client, problem = _check_request(conn, q)
            if problem:
                return problem
            iss = f"{site_of(request)}{API}"
            if form.get("decision") != "allow":
                return _redirect(q["redirect_uri"], {"error": "access_denied",
                                                     "state": q.get("state"), "iss": iss})
            scope = form.get("grant_scope") if form.get("grant_scope") in SCOPES else "write"
            code = secrets.token_urlsafe(32)
            with tx(conn):
                conn.execute("DELETE FROM oauth_codes WHERE expires_at < ?", (time.time(),))
                conn.execute(
                    "INSERT INTO oauth_codes(code_hash, client_id, redirect_uri, challenge,"
                    " scope, resource, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (_hash(code), client["client_id"], q["redirect_uri"],
                     q["code_challenge"], scope, q.get("resource"), time.time() + CODE_TTL))
            return _redirect(q["redirect_uri"], {"code": code, "state": q.get("state"),
                                                 "iss": iss})
        finally:
            conn.close()
    return await run_in_threadpool(work)


# --- public endpoints (under {prefix}/api/v1) -------------------------------------------

async def _form(request: Request) -> dict:
    if request.headers.get("content-type", "").startswith("application/json"):
        try:
            data = await request.json()
            return {k: str(v) for k, v in data.items()} if isinstance(data, dict) else {}
        except ValueError:
            return {}
    return {k: v for k, v in (await request.form()).items() if isinstance(v, str)}


def router() -> APIRouter:
    r = APIRouter()

    @r.get("/.well-known/oauth-protected-resource", include_in_schema=False)
    @r.get("/.well-known/oauth-protected-resource/mcp", include_in_schema=False)
    def protected_resource(request: Request):
        return JSONResponse(resource_metadata(site_of(request)))

    @r.get("/.well-known/oauth-authorization-server", include_in_schema=False)
    @r.get("/.well-known/openid-configuration", include_in_schema=False)
    def authorization_server(request: Request):
        return JSONResponse(server_metadata(site_of(request)))

    @r.post("/oauth/register", include_in_schema=False)
    async def register(request: Request):
        try:
            meta = await request.json()
        except ValueError:
            meta = None
        if not isinstance(meta, dict):
            return _oauth_error("invalid_client_metadata", "send the client metadata as JSON")
        uris = meta.get("redirect_uris")
        if not (isinstance(uris, list) and uris and len(uris) <= 10
                and all(isinstance(u, str) and len(u) <= 2000 and _redirect_ok(u)
                        for u in uris)):
            return _oauth_error("invalid_redirect_uri",
                                "redirect_uris: https, http on localhost, or an app scheme")
        method = meta.get("token_endpoint_auth_method") or "none"
        if method not in ("none", "client_secret_post", "client_secret_basic"):
            return _oauth_error("invalid_client_metadata",
                                f"token_endpoint_auth_method {method!r} is not supported")
        grants = meta.get("grant_types") or ["authorization_code", "refresh_token"]
        if not set(grants) <= {"authorization_code", "refresh_token"}:
            return _oauth_error("invalid_client_metadata",
                                "grant_types: authorization_code and refresh_token only")
        name = str(meta.get("client_name") or urlsplit(uris[0]).hostname or "MCP client")[:80]
        client_id = "lifeos_client_" + secrets.token_urlsafe(16)
        secret = None if method == "none" else "lifeos_cs_" + secrets.token_urlsafe(32)
        issued = int(time.time())
        out = {"client_id": client_id, "client_id_issued_at": issued, "client_name": name,
               "redirect_uris": uris, "token_endpoint_auth_method": method,
               "grant_types": grants, "response_types": ["code"]}
        if secret:
            out |= {"client_secret": secret, "client_secret_expires_at": 0}

        def save():
            conn = _db(request)
            try:
                with tx(conn):
                    count = conn.execute("SELECT COUNT(*) FROM oauth_clients").fetchone()[0]
                    if count >= MAX_CLIENTS:
                        # Drop registrations that never got a token, oldest first.
                        conn.execute(
                            "DELETE FROM oauth_clients WHERE client_id IN (SELECT client_id"
                            " FROM oauth_clients WHERE client_id NOT IN (SELECT client_id FROM"
                            " oauth_grants) ORDER BY created_at LIMIT 50)")
                    conn.execute(
                        "INSERT INTO oauth_clients(client_id, secret_hash, client_name,"
                        " redirect_uris, meta, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                        (client_id, _hash(secret) if secret else None, name, dumps(uris),
                         dumps({k: v for k, v in meta.items()
                                if k in ("client_uri", "logo_uri", "software_id",
                                         "software_version", "scope")}), utc_now()))
            finally:
                conn.close()
        await run_in_threadpool(save)
        return JSONResponse(out, status_code=201, headers=NO_STORE)

    @r.post("/oauth/token", include_in_schema=False)
    async def token(request: Request):
        form = await _form(request)
        grant = form.get("grant_type")

        def work():
            conn = _db(request)
            try:
                with tx(conn):
                    client = _authenticate_client(conn, request, form)
                    if isinstance(client, Response):
                        return client
                    if grant == "authorization_code":
                        return _code_grant(conn, client, form)
                    if grant == "refresh_token":
                        return _refresh_grant(conn, client, form)
                    return _oauth_error("unsupported_grant_type",
                                        "authorization_code or refresh_token")
            finally:
                conn.close()
        return await run_in_threadpool(work)

    @r.post("/oauth/revoke", include_in_schema=False)
    async def revoke(request: Request):
        form = await _form(request)
        value = form.get("token") or ""

        def work():
            conn = _db(request)
            try:
                with tx(conn):
                    name = None
                    g = conn.execute("SELECT token_name FROM oauth_grants WHERE refresh_hash = ?",
                                     (_hash(value),)).fetchone()
                    if g:
                        name = g["token_name"]
                    else:
                        t = conn.execute("SELECT name FROM tokens WHERE token_hash = ?",
                                         (auth._hash(value),)).fetchone()
                        if t and conn.execute("SELECT 1 FROM oauth_grants WHERE token_name = ?",
                                              (t["name"],)).fetchone():
                            name = t["name"]
                    if name:
                        conn.execute("UPDATE tokens SET revoked_at = ? WHERE name = ?"
                                     " AND revoked_at IS NULL", (utc_now(), name))
                        conn.execute("DELETE FROM oauth_grants WHERE token_name = ?", (name,))
                        log_change(conn, "oauth", "revoke", "token", name)
            finally:
                conn.close()
        await run_in_threadpool(work)
        return Response(status_code=200, headers=NO_STORE)

    return r


def _code_grant(conn, client: dict, form: dict) -> JSONResponse:
    code, verifier = form.get("code") or "", form.get("code_verifier") or ""
    row = conn.execute("SELECT * FROM oauth_codes WHERE code_hash = ?", (_hash(code),)).fetchone()
    if row is None or row["used_at"] is not None or row["expires_at"] < time.time():
        if row is not None and row["used_at"] is not None:
            # A replayed code: the token it bought may have leaked, so take it back.
            for g in conn.execute("SELECT token_name FROM oauth_grants WHERE client_id = ?",
                                  (row["client_id"],)).fetchall():
                conn.execute("UPDATE tokens SET revoked_at = ? WHERE name = ?"
                             " AND revoked_at IS NULL", (utc_now(), g["token_name"]))
        return _oauth_error("invalid_grant", "the code is unknown, used or expired")
    if row["client_id"] != client["client_id"]:
        return _oauth_error("invalid_grant", "the code was issued to another client")
    if form.get("redirect_uri") and form["redirect_uri"] != row["redirect_uri"]:
        return _oauth_error("invalid_grant", "redirect_uri does not match")
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()) \
        .rstrip(b"=").decode()
    if not verifier or not secrets.compare_digest(challenge, row["challenge"]):
        return _oauth_error("invalid_grant", "code_verifier does not match the challenge")
    conn.execute("UPDATE oauth_codes SET used_at = ? WHERE code_hash = ?",
                 (time.time(), row["code_hash"]))
    return JSONResponse(_issue(conn, client, row["scope"]), headers=NO_STORE)


def _refresh_grant(conn, client: dict, form: dict) -> JSONResponse:
    g = conn.execute("SELECT * FROM oauth_grants WHERE refresh_hash = ?",
                     (_hash(form.get("refresh_token") or ""),)).fetchone()
    if g is None or g["client_id"] != client["client_id"]:
        return _oauth_error("invalid_grant", "unknown refresh_token")
    active = conn.execute("SELECT 1 FROM tokens WHERE name = ? AND revoked_at IS NULL",
                          (g["token_name"],)).fetchone()
    if not active:  # the owner revoked it on the web page
        conn.execute("DELETE FROM oauth_grants WHERE refresh_hash = ?", (g["refresh_hash"],))
        return _oauth_error("invalid_grant", "access was revoked; sign in again")
    return JSONResponse(_issue(conn, client, g["scope"], replace=g["token_name"]),
                        headers=NO_STORE)

