MEAL = {
    "category": "meal",
    "start": "2026-11-02T12:30:00+09:00",
    "data": {"items": [
        {"name": "牛肉面", "amount": 1, "unit": "碗", "grams": 550, "kcal": 620,
         "protein_g": 30, "fat_g": 16, "carbs_g": 88},
        {"name": "煎蛋", "amount": 1, "unit": "个", "kcal": 90, "protein_g": 6.3, "fat_g": 7},
    ]},
}


def test_discovery_and_manual_are_public(client):
    client.headers.pop("Authorization")
    assert client.get("/api/v1").json()["manual"] == "http://testserver/api/v1/manual"
    manual = client.get("/llms.txt")
    assert manual.status_code == 200
    assert "http://testserver/api/v1" in manual.text and "{BASE}" not in manual.text
    assert client.get("/api/v1/openapi.json").status_code == 200


def test_auth_required_and_scoped(client, tokens):
    r = client.get("/api/v1/records", headers={"Authorization": ""})
    assert r.status_code == 401 and "hint" in r.json()["error"]
    r = client.get("/api/v1/whoami", headers={"Authorization": "Bearer lifeos_nope"})
    assert r.status_code == 401
    read = {"Authorization": f"Bearer {tokens['read']}"}
    assert client.get("/api/v1/records", headers=read).status_code == 200
    assert client.post("/api/v1/records", json=MEAL, headers=read).status_code == 403
    assert client.get("/api/v1/tokens").status_code == 403
    assert client.get("/api/v1/whoami", headers={"Authorization": "", "X-API-Key": tokens["admin"]}).json() == {
        "name": "test-admin", "scope": "admin"}


def test_meal_totals_title_and_meal_type(client):
    r = client.post("/api/v1/records", json=MEAL)
    assert r.status_code == 201, r.text
    rec = r.json()["record"]
    assert rec["data"]["totals"] == {"kcal": 710, "protein_g": 36.3, "fat_g": 23, "carbs_g": 88}
    assert rec["data"]["meal_type"] == "lunch"
    assert rec["title"] == "午餐：牛肉面、煎蛋"
    assert rec["local_date"] == "2026-11-02"
    assert rec["created_by"] == "test-write" and rec["source"] == "agent"
    assert r.json()["warnings"] == []


def test_meal_without_kcal_warns(client):
    body = {**MEAL, "data": {"items": [{"name": "苹果"}]}}
    r = client.post("/api/v1/records", json=body)
    assert r.status_code == 201
    assert "no kcal" in r.json()["warnings"][0]


def test_schema_violation_and_unknown_field(client):
    r = client.post("/api/v1/records", json={"category": "meal", "data": {"items": []}})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_data"
    assert "/categories/meal" in r.json()["error"]["hint"]
    r = client.post("/api/v1/records", json={**MEAL, "time": "12:00"})
    assert r.status_code == 422 and "time" in r.json()["error"]["message"]
    r = client.post("/api/v1/records", json={**MEAL, "category": "nope"})
    assert r.status_code == 404 and "meal" in r.json()["error"]["hint"]


def test_naive_time_uses_profile_timezone(client):
    client.patch("/api/v1/profile", json={"timezone": "Asia/Shanghai"})
    rec = client.post("/api/v1/records", json={**MEAL, "start": "2026-11-02 23:30"}).json()
    assert rec["record"]["start"] == "2026-11-02T23:30:00+08:00"
    assert rec["record"]["data"]["meal_type"] == "late_night"
    bad = client.patch("/api/v1/profile", json={"timezone": "Mars/Base"})
    assert bad.status_code == 400


def test_patch_recomputes_and_logs(client):
    rec = client.post("/api/v1/records", json=MEAL).json()["record"]
    items = [{**MEAL["data"]["items"][0], "amount": 0.5, "kcal": 310}]
    r = client.patch(f"/api/v1/records/{rec['id']}", json={"data": {"items": items}})
    assert r.status_code == 200, r.text
    after = r.json()["record"]
    assert after["data"]["totals"]["kcal"] == 310
    assert after["title"] == "午餐：牛肉面"  # auto title follows the data
    changes = client.get("/api/v1/changes", params={"entity_id": rec["id"]}).json()["changes"]
    assert [c["action"] for c in changes] == ["update", "create"]
    assert changes[0]["before"]["data"]["totals"]["kcal"] == 710


def test_custom_title_survives_patch(client):
    rec = client.post("/api/v1/records", json={**MEAL, "title": "和朋友吃面"}).json()["record"]
    r = client.patch(f"/api/v1/records/{rec['id']}", json={"note": "好吃"})
    assert r.json()["record"]["title"] == "和朋友吃面"


def test_delete_and_restore(client):
    rec = client.post("/api/v1/records", json=MEAL).json()["record"]
    client.delete(f"/api/v1/records/{rec['id']}")
    assert client.get("/api/v1/records").json()["records"] == []
    assert client.patch(f"/api/v1/records/{rec['id']}", json={"note": "x"}).status_code == 409
    client.post(f"/api/v1/records/{rec['id']}/restore")
    assert len(client.get("/api/v1/records").json()["records"]) == 1


def test_external_id_upserts(client):
    body = {**MEAL, "source": "notion", "external_id": "page-1"}
    first = client.post("/api/v1/records", json=body)
    second = client.post("/api/v1/records", json={**body, "note": "edited"})
    assert first.status_code == 201 and second.status_code == 200
    assert first.json()["record"]["id"] == second.json()["record"]["id"]
    assert second.json()["record"]["note"] == "edited"


def test_food_library_fills_items(client):
    food = client.post("/api/v1/foods", json={
        "name": "米饭", "serving": {"amount": 1, "unit": "碗", "grams": 150},
        "per_100g": {"kcal": 156, "protein_g": 2.5, "carbs_g": 35.6}}).json()
    body = {"category": "meal", "start": "2026-11-02T19:00:00+09:00", "data": {"items": [
        {"name": "米饭", "food_id": food["id"], "amount": 2, "unit": "碗"}]}}
    rec = client.post("/api/v1/records", json=body).json()["record"]
    item = rec["data"]["items"][0]
    assert item["grams"] == 300 and item["kcal"] == 468 and item["carbs_g"] == 106.8
    assert rec["data"]["meal_type"] == "dinner"
    assert client.get("/api/v1/foods", params={"q": "米"}).json()["foods"][0]["use_count"] == 1
    bad = client.post("/api/v1/foods", json={"name": "x", "per_100g": {"calories": 1}})
    assert bad.status_code == 400 and "kcal" in bad.json()["error"]["hint"]


def test_custom_category(client, tokens):
    cat = {"name": "reading", "title": "读书", "description": "Books read.",
           "schema": {"type": "object", "required": ["book"],
                      "properties": {"book": {"type": "string"}}},
           "example": {"data": {"book": "x"}}}
    assert client.post("/api/v1/categories", json=cat).status_code == 201
    assert client.post("/api/v1/categories", json=cat).status_code == 409
    r = client.post("/api/v1/records", json={"category": "reading", "data": {"book": "活着"}})
    assert r.json()["record"]["title"] == "读书"
    assert client.post("/api/v1/records", json={"category": "reading",
                                                "data": {}}).status_code == 422
    assert client.patch("/api/v1/categories/meal",
                        json={"description": "x"}).status_code == 409
    admin = {"Authorization": f"Bearer {tokens['admin']}"}
    assert client.delete("/api/v1/categories/reading", headers=admin).status_code == 409


def test_samples_dedupe_and_series(client):
    samples = [
        # iPhone and Watch both counted the same walk: the hour counts once (max source).
        {"metric": "step_count", "value": 1000, "start": "2026-11-02T09:00:00+09:00",
         "end": "2026-11-02T09:10:00+09:00", "source": "apple_health:iPhone"},
        {"metric": "step_count", "value": 1200, "start": "2026-11-02T09:05:00+09:00",
         "end": "2026-11-02T09:15:00+09:00", "source": "apple_health:Watch"},
        {"metric": "step_count", "value": 500, "start": "2026-11-02T14:00:00+09:00",
         "end": "2026-11-02T14:10:00+09:00", "source": "apple_health:iPhone"},
        {"metric": "body_mass", "value": 62.4, "start": "2026-11-01T07:00:00+09:00"},
        {"metric": "body_mass", "value": 136.0, "unit": "lb",
         "start": "2026-11-02T07:00:00+09:00"},
    ]
    r = client.post("/api/v1/samples", json={"samples": samples}).json()
    assert r["inserted"] == 5
    again = client.post("/api/v1/samples", json={"samples": samples[:1]}).json()
    assert again == {"inserted": 0, "duplicates": 1, "samples": []}
    s = client.get("/api/v1/metrics/step_count/series",
                   params={"from": "2026-11-02", "to": "2026-11-02"}).json()
    assert s["points"][0]["value"] == 1700
    w = client.get("/api/v1/metrics/weight/series",
                   params={"from": "2026-11-01", "to": "2026-11-02", "bucket": "month"}).json()
    assert w["metric"] == "body_mass" and w["points"][0]["value"] == 61.69  # lb → kg, last
    h = client.get("/api/v1/metrics/step_count/series",
                   params={"from": "2026-11-02", "to": "2026-11-02", "bucket": "hour"}).json()
    assert [p["value"] for p in h["points"]] == [1200, 500]


def test_summary_day(client):
    client.patch("/api/v1/profile", json={"goals": {"kcal": 2000, "protein_g": 100}})
    client.post("/api/v1/records", json=MEAL)
    client.post("/api/v1/samples", json={"samples": [
        {"metric": "active_energy", "value": 300, "start": "2026-11-02T10:00:00+09:00"},
        {"metric": "basal_energy", "value": 1500, "start": "2026-11-02T10:00:00+09:00"},
        {"metric": "dietary_water", "value": 500, "start": "2026-11-02T10:00:00+09:00"},
        {"metric": "sleep_core", "value": 300, "start": "2026-11-01T23:30:00+09:00",
         "end": "2026-11-02T04:30:00+09:00"},
        {"metric": "sleep_deep", "value": 60, "start": "2026-11-02T04:30:00+09:00",
         "end": "2026-11-02T05:30:00+09:00"},
    ]})
    d = client.get("/api/v1/summary/day", params={"date": "2026-11-02"}).json()
    assert d["nutrition"]["intake"]["kcal"] == 710
    assert d["nutrition"]["remaining"] == {"kcal": 1290, "protein_g": 63.7}
    assert d["energy_balance"] == {"intake_kcal": 710, "burned_kcal": 1800, "net_kcal": -1090}
    assert d["activity"]["water_ml"] == 500
    assert d["sleep"]["asleep_min"] == 360 and d["sleep"]["asleep_hours"] == 6.0
    rows = client.get("/api/v1/summary/days",
                      params={"from": "2026-11-01", "to": "2026-11-02"}).json()["days"]
    assert rows[0]["intake"] == {} and rows[1]["intake"]["kcal"] == 710
    ctx = client.get("/api/v1/context").json()
    assert ctx["profile"]["goals"]["kcal"] == 2000 and ctx["timezone"] == "Asia/Tokyo"


def test_attachments(client):
    rec = client.post("/api/v1/records", json=MEAL).json()["record"]
    r = client.post(f"/api/v1/records/{rec['id']}/attachments", params={"filename": "a.jpg"},
                    content=b"\xff\xd8jpeg", headers={"Content-Type": "image/jpeg"})
    assert r.status_code == 201, r.text
    att = r.json()
    got = client.get(f"/api/v1/attachments/{att['id']}")
    assert got.content == b"\xff\xd8jpeg" and got.headers["content-type"] == "image/jpeg"
    files = {"file": ("b.png", b"png", "image/png")}
    assert client.post(f"/api/v1/records/{rec['id']}/attachments",
                       files=files).status_code == 201
    assert len(client.get(f"/api/v1/records/{rec['id']}").json()["attachments"]) == 2


def test_list_filters_and_paging(client):
    for day in ("2026-10-30", "2026-10-31", "2026-11-01"):
        client.post("/api/v1/records", json={**MEAL, "start": f"{day}T12:00:00+09:00"})
    client.post("/api/v1/records", json={"category": "mood", "data": {"score": 8},
                                         "tags": ["good"]})
    page = client.get("/api/v1/records", params={"category": "meal", "limit": 2}).json()
    assert len(page["records"]) == 2 and page["next_cursor"]
    rest = client.get("/api/v1/records", params={"category": "meal", "limit": 2,
                                                 "cursor": page["next_cursor"]}).json()
    assert len(rest["records"]) == 1 and rest["next_cursor"] is None
    assert len(client.get("/api/v1/records", params={"from": "2026-10-31",
                                                     "to": "2026-11-01"}).json()["records"]) == 2
    assert len(client.get("/api/v1/records", params={"tag": "good"}).json()["records"]) == 1
    assert len(client.get("/api/v1/records", params={"q": "牛肉"}).json()["records"]) == 3


def test_tokens_admin(client, tokens):
    admin = {"Authorization": f"Bearer {tokens['admin']}"}
    t = client.post("/api/v1/tokens", json={"name": "phone", "scope": "read"},
                    headers=admin).json()
    assert client.get("/api/v1/whoami",
                      headers={"Authorization": f"Bearer {t['token']}"}).json()["name"] == "phone"
    client.delete("/api/v1/tokens/phone", headers=admin)
    assert client.get("/api/v1/whoami", headers={
        "Authorization": f"Bearer {t['token']}"}).status_code == 401


def test_export(client):
    client.post("/api/v1/records", json=MEAL)
    client.post("/api/v1/samples", json={"samples": [{"metric": "body_mass", "value": 60}]})
    e = client.get("/api/v1/export").json()
    assert len(e["records"]) == 1 and e["profile"]["timezone"] == "Asia/Tokyo"
    lines = client.get("/api/v1/export/samples.ndjson").text.strip().splitlines()
    assert len(lines) == 1


def test_bare_clock_time_means_today(client):
    from lifeos.timeutil import now, zone
    today = now(zone("Asia/Tokyo")).date().isoformat()
    rec = client.post("/api/v1/records", json={**MEAL, "start": "9:05"}).json()["record"]
    assert rec["start"] == f"{today}T09:05:00+09:00" and rec["data"]["meal_type"] == "breakfast"
    assert client.post("/api/v1/records", json={**MEAL, "start": "25:00"}).status_code == 400


def test_served_under_prefix_and_root(tmp_path, tokens, settings):
    from dataclasses import replace

    from fastapi.testclient import TestClient

    from lifeos.app import create_app
    auth = {"Authorization": f"Bearer {tokens['read']}"}
    with TestClient(create_app(replace(settings, prefix="/life"))) as c:
        assert c.get("/life/api/v1/whoami", headers=auth).json()["name"] == "test-read"
        assert c.get("/api/v1/whoami", headers=auth).json()["name"] == "test-read"
        assert c.get("/life/api/v1").json()["start_here"].endswith("/life/api/v1/context")
        assert c.get("/api/v1").json()["manual"] == "http://testserver/api/v1/manual"
        assert "http://testserver/life/api/v1" in c.get("/life/api/v1/manual").text
        spec = c.get("/life/api/v1/openapi.json").json()
        assert "/life/api/v1/records" in spec["paths"] and "/api/v1/records" not in spec["paths"]
        assert c.get("/api/v1/openapi.json").json() == spec
        assert c.get("/life/api/v1/docs").status_code == 200


def _public_client(settings):
    from dataclasses import replace

    from fastapi.testclient import TestClient

    from lifeos.app import create_app
    return TestClient(create_app(replace(settings, prefix="/life", public_host="yishuapp.com")),
                      base_url="https://yishuapp.com")


def test_page_is_served(client):
    page = client.get("/")
    assert page.status_code == 200 and "生活记录" in page.text
    assert "default-src 'self'" in page.headers["content-security-policy"]
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/../app.py").status_code == 404
    assert client.get("/static/nope.js").status_code == 404


def test_web_mount_signed_in_by_cloudflare_access(settings, tokens):
    access = {"Cf-Access-Jwt-Assertion": "x"}
    with _public_client(settings) as c:
        # Page and web API need the Access header on the public host.
        assert c.get("/life/").status_code == 403
        assert c.get("/life/web/api/v1/whoami").status_code == 403
        assert c.get("/life/", headers=access).status_code == 200
        me = c.get("/life/web/api/v1/whoami", headers=access).json()
        assert me == {"name": "web", "scope": "admin"}
        # Writes must come from the site itself.
        body = {"category": "mood", "data": {"score": 7}}
        assert c.post("/life/web/api/v1/records", json=body, headers=access).status_code == 403
        assert c.post("/life/web/api/v1/records", json=body, headers={
            **access, "Origin": "https://evil.example"}).status_code == 403
        ok = c.post("/life/web/api/v1/records", json=body,
                    headers={**access, "Origin": "https://yishuapp.com"})
        assert ok.status_code == 201 and ok.json()["record"]["created_by"] == "web"
        # The agent API stays open to tokens only; the Access header means nothing there.
        assert c.get("/life/api/v1/whoami", headers=access).status_code == 401
        auth = {"Authorization": f"Bearer {tokens['read']}"}
        assert c.get("/life/api/v1/whoami", headers=auth).json()["name"] == "test-read"
        assert c.get("/life/api/v1").status_code == 200


def test_web_mount_locally_takes_a_token(client, tokens):
    assert client.get("/web/api/v1/whoami").json()["name"] == "test-write"
    assert client.get("/web/api/v1/whoami", headers={"Authorization": ""}).status_code == 401
