"""The web page knows its own version, so a tab left open across a deploy can tell it is stale."""
from dataclasses import replace

from fastapi.testclient import TestClient

from lifeos import app as lifeos_app
from lifeos.app import create_app


def test_page_carries_its_version(client):
    v = client.get("/version").json()["web"]
    page = client.get("/").text
    assert f'<meta name="lifeos-version" content="{v}">' in page
    assert f"static/app.js?v={v}" in page and f"static/style.css?v={v}" in page
    assert client.get(f"/static/app.js?v={v}").status_code == 200
    assert client.get("/version").headers["cache-control"] == "no-store"


def test_version_follows_the_page_files(client, tmp_path, monkeypatch):
    web = tmp_path / "web"
    web.mkdir()
    for f in lifeos_app.WEB_DIR.iterdir():
        (web / f.name).write_bytes(f.read_bytes())
    monkeypatch.setattr(lifeos_app, "WEB_DIR", web)
    before = client.get("/version").json()["web"]
    (web / "app.js").write_text((web / "app.js").read_text() + "\n// deployed\n")
    after = client.get("/version").json()["web"]
    assert after != before and f'content="{after}"' in client.get("/").text


def test_version_sits_behind_cloudflare_access(settings, tokens):
    app = create_app(replace(settings, prefix="/life", public_host="yishuapp.com"))
    with TestClient(app, base_url="https://yishuapp.com") as c:
        assert c.get("/life/version").status_code == 403
        assert c.get("/life/version", headers={"Cf-Access-Jwt-Assertion": "x"}).status_code == 200
