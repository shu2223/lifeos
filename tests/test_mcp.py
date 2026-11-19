import base64
import hashlib
import json
import re
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from lifeos.app import create_app
from lifeos.config import Settings


def rpc(client, method, params=None, id_=1, **kw):
    msg = {"jsonrpc": "2.0", "method": method, "id": id_}
    if params is not None:
        msg["params"] = params
    return client.post("/api/v1/mcp", json=msg, **kw)


def call(client, name, args=None, **kw):
    r = rpc(client, "tools/call", {"name": name, "arguments": args or {}}, **kw)
    assert r.status_code == 200, r.text
    res = r.json()["result"]
    text = res["content"][0]["text"]
    try:
        return res["isError"], json.loads(text)
    except ValueError:
        return res["isError"], text


def test_initialize_and_list(client):
    r = rpc(client, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                   "clientInfo": {"name": "t", "version": "1"}})
    res = r.json()["result"]
    assert res["protocolVersion"] == "2025-06-18"
    assert "get_context" in res["instructions"] and res["capabilities"]["tools"]
    assert rpc(client, "initialize", {"protocolVersion": "1999-01-01"}).json()[
        "result"]["protocolVersion"] == "2025-11-25"
    n = client.post("/api/v1/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert n.status_code == 202 and n.content == b""
    tools = {t["name"]: t for t in rpc(client, "tools/list").json()["result"]["tools"]}
    assert {"get_context", "log_meal", "log_smoking", "day_summary", "update_record"} <= set(tools)
    assert tools["get_context"]["annotations"]["readOnlyHint"] is True
    assert tools["delete_record"]["annotations"]["destructiveHint"] is True
    assert "mood (心情)" in tools["log_record"]["description"]
    assert tools["log_meal"]["inputSchema"]["properties"]["items"]["items"]["properties"]["kcal"]
    assert client.get("/api/v1/mcp").status_code == 405
    assert client.get("/api/v1").json()["mcp"] == "http://testserver/api/v1/mcp"


def test_log_meal_then_summary_and_fix(client):
    err, out = call(client, "log_meal", {"start": "2026-11-02T12:30:00+09:00", "items": [
        {"name": "牛肉面", "grams": 550, "kcal": 620, "protein_g": 30, "fat_g": 16,
         "carbs_g": 88, "estimated": True, "confidence": "medium"}], "note": "中午吃了牛肉面"})
    assert not err, out
    rec = out["record"]
    assert rec["category"] == "meal" and rec["data"]["totals"]["kcal"] == 620
    assert rec["created_by"] == "test-write" and rec["note"] == "中午吃了牛肉面"
    err, day = call(client, "day_summary", {"date": "2026-11-02"})
    assert not err and day["date"] == "2026-11-02"
    err, out = call(client, "update_record", {"id": rec["id"], "title": "午饭"})
    assert not err and out["record"]["title"] == "午饭"
    err, out = call(client, "search_records", {"category": "meal", "date": "2026-11-02"})
    assert [r["id"] for r in out["records"]] == [rec["id"]]
    err, out = call(client, "delete_record", {"id": rec["id"]})
    assert not err and out["record"]["deleted_at"]


def test_smoking_measurements_and_errors(client):
    err, out = call(client, "log_smoking", {})
    assert not err and out["record"]["data"]["count"] == 1
    err, out = call(client, "log_measurements", {"samples": [{"metric": "body_mass",
                                                             "value": 61.8}]})
    assert not err, out
    # Arguments are checked against the tool's schema; the model sees why.
    err, out = call(client, "log_meal", {"items": []})
    assert err and "items" in out
    err, out = call(client, "get_record", {"id": "rec_nope"})
    assert err and out["status"] == 404
    r = rpc(client, "tools/call", {"name": "nope", "arguments": {}})
    assert r.json()["error"]["code"] == -32602
    assert rpc(client, "no/such").json()["error"]["code"] == -32601
    err, text = call(client, "read_manual")
    assert not err and text.startswith("# lifeos")
    res = rpc(client, "resources/read", {"uri": "lifeos://manual"}).json()["result"]
    assert res["contents"][0]["text"].startswith("# lifeos")


def test_batch_and_scope(client, tokens):
    r = client.post("/api/v1/mcp", json=[
        {"jsonrpc": "2.0", "id": 1, "method": "ping"},
        {"jsonrpc": "2.0", "method": "notifications/initialized"}])
    assert r.json() == [{"jsonrpc": "2.0", "id": 1, "result": {}}]
    read = {"Authorization": f"Bearer {tokens['read']}"}
    err, out = call(client, "log_smoking", {}, headers=read)
    assert err and out["status"] == 403
    err, _ = call(client, "get_context", headers=read)
    assert not err


def test_unauthorized_points_to_oauth(client):
    r = rpc(client, "initialize", headers={"Authorization": ""})
    assert r.status_code == 401
    challenge = r.headers["www-authenticate"]
    assert 'resource_metadata="http://testserver/api/v1/.well-known/oauth-protected-resource"' \
        in challenge
    r = rpc(client, "ping", headers={"Authorization": "Bearer lifeos_nope"})
    assert r.status_code == 401 and 'error="invalid_token"' in r.headers["www-authenticate"]
    prm = client.get("/api/v1/.well-known/oauth-protected-resource").json()
    assert prm["resource"] == "http://testserver/api/v1/mcp"
    meta = client.get("/api/v1/.well-known/oauth-authorization-server").json()
    assert meta["issuer"] == prm["authorization_servers"][0]
    assert meta["authorization_endpoint"] == "http://testserver/oauth/authorize"
    assert client.get("/api/v1/.well-known/openid-configuration").json() == meta
    assert client.get("/.well-known/oauth-authorization-server/api/v1").json() == meta
    assert client.get("/.well-known/oauth-protected-resource/api/v1/mcp").json() == prm
    assert client.get("/.well-known/oauth-protected-resource/other").status_code == 404


# --- OAuth ----------------------------------------------------------------------------------

REDIRECT = "https://claude.ai/api/mcp/auth_callback"
VERIFIER = "v" * 64
CHALLENGE = base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).rstrip(
    b"=").decode()


def register(c, **meta):
    r = c.post("/api/v1/oauth/register", json={"client_name": "Claude", "redirect_uris": [REDIRECT],
                                               "token_endpoint_auth_method": "none", **meta})
    assert r.status_code == 201, r.text
    return r.json()


def auth_query(client_id, **kw):
    return {"response_type": "code", "client_id": client_id, "redirect_uri": REDIRECT,
            "code_challenge": CHALLENGE, "code_challenge_method": "S256", "state": "xyz",
            "scope": "write", **kw}


def form_token(c, query, path="/oauth/authorize", headers=None):
    page = c.get(path, params=query, headers=headers or {})
    assert page.status_code == 200, page.text
    return re.search(r'name="form_token" value="([^"]+)"', page.text).group(1)


def approve(c, client_id, decision="allow", headers=None, **kw):
    q = auth_query(client_id, **kw)
    r = c.post("/oauth/authorize", data={**q, "decision": decision, "form_token": form_token(c, q),
                                         "grant_scope": kw.get("scope", "write")},
               headers=headers or {}, follow_redirects=False)
    assert r.status_code == 303, r.text
    loc = urlsplit(r.headers["location"])
    return loc, {k: v[0] for k, v in parse_qs(loc.query).items()}


def test_oauth_full_flow(client, tokens):
    c = client
    c.headers.pop("Authorization")
    reg = register(c)
    page = c.get("/oauth/authorize", params=auth_query(reg["client_id"]))
    assert page.status_code == 200 and "Claude" in page.text and "claude.ai" in page.text
    loc, q = approve(c, reg["client_id"])
    assert f"{loc.scheme}://{loc.netloc}{loc.path}" == REDIRECT
    assert q["state"] == "xyz" and q["iss"] == "http://testserver/api/v1"
    form = {"grant_type": "authorization_code", "code": q["code"], "redirect_uri": REDIRECT,
            "client_id": reg["client_id"], "code_verifier": VERIFIER}
    tok = c.post("/api/v1/oauth/token", data=form)
    assert tok.status_code == 200, tok.text
    tok = tok.json()
    assert tok["token_type"] == "Bearer" and tok["scope"] == "write"
    bearer = {"Authorization": f"Bearer {tok['access_token']}"}
    err, out = call(c, "log_smoking", {}, headers=bearer)
    assert not err and out["record"]["created_by"].startswith("claude-")
    # A code works once; replaying it also takes back the token it bought.
    assert c.post("/api/v1/oauth/token", data=form).json()["error"] == "invalid_grant"
    assert rpc(c, "ping", headers=bearer).status_code == 401


def test_oauth_refresh_and_revoke(client, conn):
    c = client
    c.headers.pop("Authorization")
    reg = register(c, client_name="ChatGPT")
    _, q = approve(c, reg["client_id"], scope="read")
    tok = c.post("/api/v1/oauth/token", data={
        "grant_type": "authorization_code", "code": q["code"], "client_id": reg["client_id"],
        "code_verifier": VERIFIER}).json()
    assert tok["scope"] == "read"
    err, out = call(c, "log_smoking", {}, headers={"Authorization": f"Bearer {tok['access_token']}"})
    assert err and out["status"] == 403
    new = c.post("/api/v1/oauth/token", data={
        "grant_type": "refresh_token", "refresh_token": tok["refresh_token"],
        "client_id": reg["client_id"]}).json()
    assert new["access_token"] != tok["access_token"]
    assert rpc(c, "ping", headers={"Authorization": f"Bearer {tok['access_token']}"}) \
        .status_code == 401
    assert rpc(c, "ping", headers={"Authorization": f"Bearer {new['access_token']}"}) \
        .status_code == 200
    names = [r["name"] for r in conn.execute(
        "SELECT name FROM tokens WHERE revoked_at IS NULL AND name LIKE 'chatgpt-%'")]
    assert len(names) == 1
    # Revoked on the web page: refreshing no longer works.
    conn.execute("UPDATE tokens SET revoked_at = 'x' WHERE name = ?", (names[0],))
    r = c.post("/api/v1/oauth/token", data={
        "grant_type": "refresh_token", "refresh_token": new["refresh_token"],
        "client_id": reg["client_id"]})
    assert r.status_code == 400 and r.json()["error"] == "invalid_grant"


def test_oauth_rejections(client):
    c = client
    c.headers.pop("Authorization")
    assert c.post("/api/v1/oauth/register", json={"redirect_uris": ["http://evil.com/cb"]}) \
        .status_code == 400
    assert c.post("/api/v1/oauth/register", json={"redirect_uris": ["javascript:alert(1)"]}) \
        .status_code == 400
    reg = register(c)
    # Unknown redirect: an error page, never a redirect to it.
    r = c.get("/oauth/authorize", params=auth_query(reg["client_id"],
                                                     redirect_uri="https://evil.com/cb"))
    assert r.status_code == 400 and "location" not in r.headers
    # No PKCE: back to the client with an error.
    r = c.get("/oauth/authorize", params={**auth_query(reg["client_id"]), "code_challenge": ""},
              follow_redirects=False)
    assert r.status_code == 303 and "error=invalid_request" in r.headers["location"]
    loc, q = approve(c, reg["client_id"], decision="deny")
    assert q["error"] == "access_denied" and "code" not in q
    _, q = approve(c, reg["client_id"])
    bad = c.post("/api/v1/oauth/token", data={
        "grant_type": "authorization_code", "code": q["code"], "client_id": reg["client_id"],
        "code_verifier": "wrong" * 10})
    assert bad.json()["error"] == "invalid_grant"
    other = register(c)
    r = c.post("/api/v1/oauth/token", data={
        "grant_type": "authorization_code", "code": q["code"], "client_id": other["client_id"],
        "code_verifier": VERIFIER})
    assert r.json()["error"] == "invalid_grant"
    # Loopback redirects may change port (RFC 8252); confidential clients need their secret.
    local = c.post("/api/v1/oauth/register", json={
        "client_name": "Claude Code", "redirect_uris": ["http://localhost:3118/callback"],
        "token_endpoint_auth_method": "client_secret_post"}).json()
    assert local["client_secret"]
    lq = auth_query(local["client_id"], redirect_uri="http://localhost:50000/callback")
    r = c.post("/oauth/authorize", data={**lq, "decision": "allow",
                                         "form_token": form_token(c, lq)},
               follow_redirects=False)
    code = parse_qs(urlsplit(r.headers["location"]).query)["code"][0]
    form = {"grant_type": "authorization_code", "code": code, "client_id": local["client_id"],
            "code_verifier": VERIFIER}
    assert c.post("/api/v1/oauth/token", data=form).status_code == 401
    ok = c.post("/api/v1/oauth/token", data={**form, "client_secret": local["client_secret"]})
    assert ok.status_code == 200


@pytest.fixture
def public(tmp_path, tokens, settings):
    s = Settings(home=settings.home, prefix="/life", public_host="yishuapp.com")
    with TestClient(create_app(s), base_url="https://yishuapp.com") as c:
        yield c


def test_public_host_consent_needs_access(public):
    reg = public.post("/life/api/v1/oauth/register",
                      json={"client_name": "Claude", "redirect_uris": [REDIRECT]}).json()
    params = auth_query(reg["client_id"])
    # Without Cloudflare Access the page never opens.
    assert public.get("/life/oauth/authorize", params=params).status_code == 403
    signed = {"cf-access-jwt-assertion": "x"}
    page = public.get("/life/oauth/authorize", params=params, headers=signed)
    assert page.status_code == 200
    page_url = "/life/oauth/authorize"
    data = {**params, "decision": "allow"}
    tok = form_token(public, params, page_url, signed)
    # Another site is refused even with a token; the token is single-use either way.
    r = public.post(page_url, data={**data, "form_token": tok}, follow_redirects=False,
                    headers={**signed, "origin": "https://evil.com"})
    assert r.status_code == 403
    # Without the page's token (a forged form), refused whatever the Origin.
    r = public.post(page_url, data=data, follow_redirects=False,
                    headers={**signed, "origin": "https://yishuapp.com"})
    assert r.status_code == 403
    # Safari sends Origin: null; the token proves the form came from the page.
    tok = form_token(public, params, page_url, signed)
    r = public.post(page_url, data={**data, "form_token": tok}, follow_redirects=False,
                    headers={**signed, "origin": "null"})
    assert r.status_code == 303
    r = public.post(page_url, data={**data, "form_token": tok}, follow_redirects=False,
                    headers={**signed, "origin": "https://yishuapp.com"})
    assert r.status_code == 403  # used once already
    tok = form_token(public, params, page_url, signed)
    r = public.post(page_url, data={**data, "form_token": tok}, follow_redirects=False,
                    headers={**signed, "origin": "https://yishuapp.com"})
    assert r.status_code == 303
    page = public.get(page_url, params=params, headers=signed)
    assert page.headers["referrer-policy"] == "same-origin"
    assert "iss=https%3A%2F%2Fyishuapp.com%2Flife%2Fapi%2Fv1" in r.headers["location"]
    # Discovery is public, at the prefixed API root and at the site root.
    meta = public.get("/life/api/v1/.well-known/oauth-authorization-server").json()
    assert meta["authorization_endpoint"] == "https://yishuapp.com/life/oauth/authorize"
    assert meta["token_endpoint"] == "https://yishuapp.com/life/api/v1/oauth/token"
    assert public.get("/.well-known/oauth-authorization-server/life/api/v1").json() == meta
    prm = public.get("/.well-known/oauth-protected-resource/life/api/v1/mcp").json()
    assert prm["resource"] == "https://yishuapp.com/life/api/v1/mcp"
    r = public.post("/life/api/v1/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert r.status_code == 401 and "https://yishuapp.com/life/api/v1/.well-known/" \
        in r.headers["www-authenticate"]


MODERN = "2026-07-28"


def modern(client, method, params=None, headers=None, **kw):
    meta = {"io.modelcontextprotocol/protocolVersion": MODERN,
            "io.modelcontextprotocol/clientCapabilities": {}}
    h = {"MCP-Protocol-Version": MODERN, "Mcp-Method": method}
    if method == "tools/call":
        h["Mcp-Name"] = params["name"]
    return client.post("/api/v1/mcp", json={"jsonrpc": "2.0", "id": 7, "method": method,
                                            "params": {**(params or {}), "_meta": meta}},
                       headers={**h, **(headers or {})}, **kw)


def test_modern_protocol(client):
    d = modern(client, "server/discover").json()["result"]
    assert MODERN in d["supportedVersions"] and "2025-11-25" in d["supportedVersions"]
    assert d["resultType"] == "complete" and d["ttlMs"] == 0 and d["cacheScope"] == "private"
    assert d["_meta"]["io.modelcontextprotocol/serverInfo"]["name"] == "lifeos"
    tools = modern(client, "tools/list").json()["result"]
    assert tools["cacheScope"] == "private" and len(tools["tools"]) == len(
        rpc(client, "tools/list").json()["result"]["tools"])
    r = modern(client, "tools/call", {"name": "log_smoking", "arguments": {}}).json()["result"]
    assert r["resultType"] == "complete" and not r["isError"] and "ttlMs" not in r
    # Headers must agree with the envelope; unknown versions name the supported ones.
    r = modern(client, "tools/call", {"name": "log_smoking", "arguments": {}},
               headers={"Mcp-Name": "delete_record"})
    assert r.status_code == 400 and r.json()["error"]["code"] == -32020
    r = client.post("/api/v1/mcp", headers={"MCP-Protocol-Version": "2099-01-01",
                                            "Mcp-Method": "tools/list"},
                    json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {"_meta": {
                        "io.modelcontextprotocol/protocolVersion": "2099-01-01",
                        "io.modelcontextprotocol/clientCapabilities": {}}}})
    assert r.status_code == 400 and r.json()["error"]["code"] == -32022
    assert MODERN in r.json()["error"]["data"]["supported"]
    assert modern(client, "no/such").status_code == 404
