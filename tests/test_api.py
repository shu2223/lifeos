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


EXPORT, AUTO = "apple_health:Apple Watch", "health_auto_export:unknown"


def _sample(metric, source, d, start, end, value, end_day=None, offset="+09:00"):
    return {"metric": metric, "value": value, "source": source, "start": f"{d}T{start}:00{offset}",
            "end": f"{end_day or d}T{end}:00{offset}"}


def _values(client, metric, d, to=None, **params):
    return [p["value"] for p in client.get(f"/api/v1/metrics/{metric}/series", params={
        "from": d, "to": to or d, **params}).json()["points"]]


def test_export_and_auto_export_count_once(client):
    """The export file and Health Auto Export carry the same Apple Health data but bucket it
    differently, so the larger value of each hour would add up to more than either has."""
    def burn(source, d, start, end, value):
        return _sample("basal_energy", source, d, start, end, value)

    def series(d, **params):
        return _values(client, "basal_energy", d, **params)

    d = "2026-10-10"
    client.post("/api/v1/samples", json={"samples": [
        burn(EXPORT, d, "10:50", "11:20", 60), burn(EXPORT, d, "11:30", "11:50", 40),  # 100
        burn(AUTO, d, "10:00", "11:00", 40), burn(AUTO, d, "11:00", "12:00", 50)]})  # 90
    assert series(d) == [100]  # the export alone, not 60 + 50
    assert series(d, bucket="hour") == [60, 40]
    assert client.get("/api/v1/summary/day", params={"date": d}).json()["activity"]["basal_kcal"] == 100
    f = "2026-10-11"  # the export stops early: the other fills only the hours it lacks
    client.post("/api/v1/samples", json={"samples": [
        burn(EXPORT, f, f"{h:02d}:00", f"{h:02d}:59", 80) for h in range(0, 3)] + [
        burn(AUTO, f, f"{h:02d}:00", f"{h:02d}:59", 70) for h in range(2, 5)]})
    assert series(f) == [380] and series(f, bucket="hour") == [80, 80, 80, 70, 70]
    g = "2026-10-12"  # one sample runs into 11:00; the other copy's 11:00 share is the same burn
    client.post("/api/v1/samples", json={"samples": [
        burn(EXPORT, g, "10:30", "11:50", 110), burn(AUTO, g, "10:00", "10:00", 50),
        burn(AUTO, g, "11:00", "11:00", 59)]})
    assert series(g) == [110] and series(g, bucket="hour") == [110]
    e = "2026-10-13"  # the export ends at 21:43; its last hour is short, the other copy's is whole
    client.post("/api/v1/samples", json={"samples": [
        burn(EXPORT, e, f"{h:02d}:00", f"{h:02d}:59", 80) for h in range(21)] + [
        burn(EXPORT, e, "21:00", "21:43", 50)] + [
        burn(AUTO, e, f"{h:02d}:00", f"{h:02d}:00", 70) for h in range(18, 24)]})
    assert series(e) == [21 * 80 + 70 + 2 * 70]


def test_sleep_counts_once_and_by_the_morning(client):
    def core(source, d, start, end, value, end_day=None):
        return _sample("sleep_core", source, d, start, end, value, end_day)

    client.post("/api/v1/samples", json={"samples": [
        core(EXPORT, "2026-10-19", "23:30", "23:50", 20),  # before midnight: still last night
        core(EXPORT, "2026-10-20", "01:30", "02:30", 60), core(EXPORT, "2026-10-20", "03:00", "04:00", 70),
        core(AUTO, "2026-10-19", "23:20", "07:30", 155, end_day="2026-10-20")]})  # one point a night
    assert _values(client, "sleep_core", "2026-10-19") == []
    assert _values(client, "sleep_core", "2026-10-20") == [155]
    assert sum(_values(client, "sleep_core", "2026-10-19", "2026-10-20", bucket="hour")) == 155
    sleep = client.get("/api/v1/summary/day", params={"date": "2026-10-20"}).json()["sleep"]
    assert sleep["core_min"] == 155


def test_hour_series_keeps_one_device_per_hour_across_dates(client):
    # 00:10 at +09:00 and 16:20 at +01:00 the evening before are the same hour on the clock
    client.post("/api/v1/samples", json={"samples": [
        {"metric": "step_count", "value": 100, "source": "agent", "start": "2026-10-16T00:10:00+09:00"},
        {"metric": "step_count", "value": 100, "source": "manual", "start": "2026-10-15T16:20:00+01:00"}]})
    assert _values(client, "step_count", "2026-10-15", "2026-10-16", bucket="hour") == [100]


def test_old_sleep_rows_move_to_the_morning(tmp_path):
    from lifeos import db
    conn = db.connect(tmp_path / "old.db")
    conn.executescript(db.SCHEMA)
    conn.executescript(db.MIGRATIONS[2])
    conn.execute("PRAGMA user_version=2")
    conn.execute("INSERT INTO metrics(name, title, unit, aggregation, date_by, created_at)"
                 " VALUES ('sleep_core', '核心睡眠', 'min', 'sum', 'end', 'x')")
    for i, (start, end, d) in enumerate((
            ("2026-10-19T23:30:00+09:00", "2026-10-19T23:50:00+09:00", "2026-10-19"),
            ("2026-10-20T01:30:00+09:00", "2026-10-20T02:30:00+09:00", "2026-10-20"),
            ("2026-10-20T14:00:00+09:00", "2026-10-20T14:40:00+09:00", "2026-10-20"))):
        conn.execute("INSERT INTO samples(metric, start_at, end_at, start_ts, end_ts, local_date,"
                     " value, unit, source, created_by, created_at) VALUES ('sleep_core', ?, ?, ?, ?,"
                     " ?, 1, 'min', 'x', 'x', 'x')", (start, end, i, i, d))
    db.migrate(conn)
    dates = [r[0] for r in conn.execute("SELECT local_date FROM samples ORDER BY start_at")]
    assert dates == ["2026-10-20", "2026-10-20", "2026-10-20"]  # the nap stays on its own day


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


def test_smoking_daily_counts(client):
    from datetime import datetime, timedelta

    from lifeos.timeutil import now, zone
    today = now(zone("Asia/Tokyo")).date()
    day = lambda n: (today - timedelta(days=n)).isoformat()  # noqa: E731
    client.patch("/api/v1/profile", json={"goals": {"cigarettes_max": 5}})
    post = lambda **body: client.post("/api/v1/records", json={"category": "smoking", **body})  # noqa: E731
    assert client.get("/api/v1/summary/day").json()["smoking"] is None  # not tracking yet
    assert post(start=f"{day(3)}T20:00:00+09:00", data={"count": 3}).status_code == 201
    for clock in ("09:00", "13:00"):
        post(start=f"{day(1)}T{clock}:00+09:00", data={"count": 1})
    tap = post(source="manual", data={"count": 1}).json()["record"]  # the web page's +1
    assert tap["title"] == "抽烟 1 根" and tap["local_date"] == today.isoformat()
    extra = post(source="manual", data={"count": 1}).json()["record"]
    heated = post(start=f"{today}T00:05:00+09:00", data={"count": 2, "kind": "heated"})
    assert heated.json()["record"]["title"] == "抽烟 2 根（加热烟）"
    client.delete(f"/api/v1/records/{extra['id']}")  # the −1 button
    assert post(data={"count": 0}).status_code == 422
    assert post(data={"kind": "cigarette"}).status_code == 422

    d = client.get("/api/v1/summary/day").json()
    sm = d["smoking"]
    assert sm["cigarettes"] == 3 and sm["daily_max"] == 5 and sm["yesterday"] == 2
    assert sm["avg_prev_7d"] == round((3 + 0 + 2) / 3, 1)  # the smoke-free day counts as 0
    assert sm["tracking_since"] == day(3)
    assert sorted(e["count"] for e in sm["entries"]) == [1, 2]
    assert sm["last_at"] == max((e["start"] for e in sm["entries"]), key=datetime.fromisoformat)
    assert all(r["category"] != "smoking" for r in d["other_records"])
    assert client.get("/api/v1/summary/day", params={"date": day(4)}).json()["smoking"] is None
    past = client.get("/api/v1/summary/day", params={"date": day(2)}).json()["smoking"]
    assert past["cigarettes"] == 0 and past["entries"] == [] and past["yesterday"] == 3
    assert past["last_at"] == f"{day(3)}T20:00:00+09:00"

    tomorrow = (today + timedelta(days=1)).isoformat()
    rows = client.get("/api/v1/summary/days", params={"from": day(5), "to": tomorrow}).json()
    assert [r.get("cigarettes") for r in rows["days"]] == [None, None, 3, 0, 2, 3, None]
    ctx = client.get("/api/v1/context").json()
    assert ctx["today_summary"]["smoking"]["cigarettes"] == 3
    assert "smoking" in [c["name"] for c in ctx["categories"]]
    assert client.get("/api/v1/summary/day", params={"date": "0001-01-03"}).status_code == 200


def test_smoking_edge_cases(client):
    from datetime import timedelta

    from lifeos.timeutil import iso, now, zone
    rec = client.post("/api/v1/records", json={"category": "smoking", "data": {"count": 2.0}})
    assert rec.json()["record"]["title"] == "抽烟 2 根" and rec.json()["record"]["data"]["count"] == 2
    t = now(zone("Asia/Tokyo"))
    later = t + timedelta(hours=1)
    client.post("/api/v1/records", json={"category": "smoking", "start": iso(later), "data": {"count": 3}})
    sm = client.get("/api/v1/summary/day").json()["smoking"]
    if later.date() == t.date():  # an entry timed later today is counted but is not the last one yet
        assert sm["cigarettes"] == 5 and sm["last_at"] == rec.json()["record"]["start"]


def test_goals_merge(client):
    client.patch("/api/v1/profile", json={"goals": {"kcal": 2000, "steps": 8000}})
    goals = client.patch("/api/v1/profile", json={"goals": {"cigarettes_max": 8}}).json()["profile"]["goals"]
    assert goals == {"kcal": 2000, "steps": 8000, "cigarettes_max": 8}
    goals = client.patch("/api/v1/profile", json={"goals": {"steps": None}}).json()["profile"]["goals"]
    assert goals == {"kcal": 2000, "cigarettes_max": 8}
    prof = client.patch("/api/v1/profile", json={"goals": {"kcal": None, "cigarettes_max": None}})
    assert "goals" not in prof.json()["profile"]


def _energy_fixture(client):
    """30 days of 2,000 kcal resting and 300 active burn before today, so with a 1,500 goal the
    line sits at 1,500 − 2,300 = −800. Yesterday 1,600 eaten; today 2,000 eaten, 800 resting
    and 200 active so far. Walks at 4.0, 5.0 and 4.5 kcal/min, 100 and 120 steps/min."""
    from datetime import timedelta

    from lifeos.timeutil import now, zone
    today = now(zone("Asia/Tokyo")).date()
    day = lambda n: (today - timedelta(days=n)).isoformat()  # noqa: E731
    client.patch("/api/v1/profile", json={"goals": {"kcal": 1500}})
    samples = []
    for n in range(1, 31):
        for metric, value in (("basal_energy", 2000), ("active_energy", 300)):
            samples.append({"metric": metric, "value": value, "unit": "kcal",
                            "start": f"{day(n)}T12:00:00+09:00"})
    samples += [{"metric": "basal_energy", "value": 800, "unit": "kcal",
                 "start": f"{day(0)}T00:00:00+09:00", "end": f"{day(0)}T10:00:00+09:00"},
                {"metric": "active_energy", "value": 200, "unit": "kcal",
                 "start": f"{day(0)}T09:00:00+09:00"}]
    assert client.post("/api/v1/samples", json={"samples": samples}).json()["inserted"] == 62
    for n, clock, kcal in ((1, "08:00", 1600), (0, "00:00", 2000)):  # today's is already past
        client.post("/api/v1/records", json={"category": "meal", "start": f"{day(n)}T{clock}:00+09:00",
                                             "data": {"items": [{"name": "饭", "kcal": kcal}]}})
    for minutes, kcal, steps in ((30, 120, 3000), (20, 100, 2400), (10, 45, None)):
        data = {"activity_type": "walking", "duration_min": minutes, "active_kcal": kcal}
        if steps:
            data["step_count"] = steps
        client.post("/api/v1/records", json={"category": "workout", "data": data,
                                             "start": f"{day(3)}T18:00:00+09:00"})
    return day


def test_energy_bar_today(client):
    _energy_fixture(client)
    assert client.get("/api/v1/summary/day").json()["energy"] == {
        "logged": True, "intake_kcal": 2000, "basal_kcal": 800, "basal_expected_kcal": 2000,
        "active_kcal": 200,
        "carry_kcal": 100,  # yesterday ended at 1,600 − 2,300 = −700, 100 short of its line
        "balance_kcal": 1100,  # 100 + 2,000 − 800 − 200
        "balance_tonight_kcal": -100,  # 1,200 more resting burn to come before midnight
        "average_burn_kcal": 2300, "target_kcal": -800, "over_target_kcal": 700,
        "walk_kcal_per_min": 4.5, "walk_steps_per_min": 110,
        "walk_minutes": 156, "walk_steps": 17100,  # 700 / 4.5 = 155.6 minutes × 110 steps
        "final": False}


def test_energy_bar_past_days(client):
    day = _energy_fixture(client)
    y = client.get("/api/v1/summary/day", params={"date": day(1)}).json()["energy"]
    assert y["final"] is True and y["carry_kcal"] is None  # the day before it has no meals
    assert (y["balance_kcal"], y["balance_tonight_kcal"], y["over_target_kcal"]) == (-700, -700, 100)
    assert y["walk_minutes"] is None and y["walk_steps"] is None
    blank = client.get("/api/v1/summary/day", params={"date": day(2)}).json()["energy"]
    assert blank["logged"] is False and blank["intake_kcal"] is None
    assert blank["balance_kcal"] is None and blank["over_target_kcal"] is None
    assert (blank["basal_kcal"], blank["active_kcal"], blank["target_kcal"]) == (2000, 300, -800)
    # a day whose resting burn never synced falls back to the 7-day average
    gone = client.get("/api/v1/samples", params={"metric": "basal_energy", "from": day(1),
                                                  "to": day(1)}).json()["samples"][0]
    client.delete(f"/api/v1/samples/{gone['id']}")
    y = client.get("/api/v1/summary/day", params={"date": day(1)}).json()["energy"]
    assert (y["basal_kcal"], y["balance_kcal"]) == (2000, -700)


def test_energy_meal_timed_later_today_counts_tonight_not_now(client):
    from datetime import timedelta

    from lifeos.timeutil import iso, now, zone
    _energy_fixture(client)
    t = now(zone("Asia/Tokyo"))
    later = t + timedelta(hours=1)
    if later.date() != t.date():
        return  # nothing later today to log after 23:00
    client.post("/api/v1/records", json={"category": "meal", "start": iso(later),
                                         "data": {"items": [{"name": "晚饭", "kcal": 500}]}})
    e = client.get("/api/v1/summary/day").json()["energy"]
    assert (e["intake_kcal"], e["balance_kcal"]) == (2500, 1100)  # not eaten yet
    assert (e["balance_tonight_kcal"], e["over_target_kcal"]) == (400, 1200)


def test_energy_resting_top_up_falls_back_to_the_month(client):
    from datetime import timedelta

    from lifeos.timeutil import now, zone
    day = lambda n: (now(zone("Asia/Tokyo")).date() - timedelta(days=n)).isoformat()  # noqa: E731
    client.patch("/api/v1/profile", json={"goals": {"kcal": 1500}})
    samples = [{"metric": m, "value": v, "unit": "kcal", "start": f"{day(n)}T12:00:00+09:00"}
               for n in range(8, 31) for m, v in (("basal_energy", 2000), ("active_energy", 300))]
    samples.append({"metric": "basal_energy", "value": 900, "unit": "kcal",
                    "start": f"{day(0)}T00:00:00+09:00", "end": f"{day(0)}T11:00:00+09:00"})
    client.post("/api/v1/samples", json={"samples": samples})
    client.post("/api/v1/records", json={"category": "meal", "start": f"{day(0)}T00:00:00+09:00",
                                         "data": {"items": [{"name": "饭", "kcal": 1500}]}})
    e = client.get("/api/v1/summary/day").json()["energy"]
    assert e["basal_expected_kcal"] == 2000  # no basal in the last week; the month's average
    assert (e["balance_kcal"], e["balance_tonight_kcal"], e["over_target_kcal"]) == (600, -500, 300)
    assert (e["walk_minutes"], e["walk_steps"]) == (75, 7500)  # default 4 kcal and 100 steps a minute


def test_energy_needs_resting_burn_to_say_anything(client):
    from datetime import timedelta

    from lifeos.timeutil import now, zone
    day = lambda n: (now(zone("Asia/Tokyo")).date() - timedelta(days=n)).isoformat()  # noqa: E731
    client.patch("/api/v1/profile", json={"goals": {"kcal": 1500}})
    client.post("/api/v1/samples", json={"samples": [
        {"metric": "active_energy", "value": 300, "unit": "kcal", "start": f"{day(n)}T12:00:00+09:00"}
        for n in range(0, 31)]})
    client.post("/api/v1/records", json={"category": "meal", "start": f"{day(0)}T00:00:00+09:00",
                                         "data": {"items": [{"name": "饭", "kcal": 1500}]}})
    e = client.get("/api/v1/summary/day").json()["energy"]
    assert e["logged"] is True and e["basal_expected_kcal"] is None
    assert e["balance_kcal"] is None and e["balance_tonight_kcal"] is None
    assert e["over_target_kcal"] is None and e["walk_minutes"] is None


def test_walk_pace_comes_from_walks_of_five_minutes_or_more(client):
    from datetime import timedelta

    from lifeos.timeutil import now, zone
    day = lambda n: (now(zone("Asia/Tokyo")).date() - timedelta(days=n)).isoformat()  # noqa: E731
    walk = lambda d, minutes, kcal, steps: client.post("/api/v1/records", json={  # noqa: E731
        "category": "workout", "start": f"{d}T18:00:00+09:00",
        "data": {"activity_type": "walking", "duration_min": minutes, "active_kcal": kcal,
                 "step_count": steps}})
    for _ in range(10):
        walk(day(10), 30, 180, 3600)  # 6 kcal and 120 steps a minute
    for _ in range(30):
        walk(day(1), 3, 9, 240)  # newer, but too short to say anything about pace
    e = client.get("/api/v1/summary/day").json()["energy"]
    assert (e["walk_kcal_per_min"], e["walk_steps_per_min"]) == (6.0, 120)


def test_energy_carry_reaches_back_one_day(client):
    day = _energy_fixture(client)
    client.post("/api/v1/records", json={"category": "meal", "start": f"{day(2)}T08:00:00+09:00",
                                         "data": {"items": [{"name": "饭", "kcal": 3000}]}})
    y = client.get("/api/v1/summary/day", params={"date": day(1)}).json()["energy"]
    assert y["carry_kcal"] == 1500  # 3,000 − 2,300 = +700 against a −800 line
    assert client.get("/api/v1/summary/day").json()["energy"]["carry_kcal"] == 100  # not 1,600


def test_energy_bar_goal_changes_the_line(client):
    _energy_fixture(client)
    client.patch("/api/v1/profile", json={"goals": {"kcal": None}})
    e = client.get("/api/v1/summary/day").json()["energy"]
    assert e["target_kcal"] is None and e["carry_kcal"] is None and e["over_target_kcal"] is None
    assert e["balance_tonight_kcal"] == -200 and e["walk_minutes"] is None  # 2,000 − 2,000 − 200
    client.patch("/api/v1/profile", json={"goals": {"kcal": 2500}})
    e = client.get("/api/v1/summary/day").json()["energy"]
    assert e["target_kcal"] == 200 and e["carry_kcal"] == -900  # yesterday −700 against +200
    assert e["balance_tonight_kcal"] == -1100 and e["over_target_kcal"] == -1300  # room left
    assert e["walk_minutes"] is None and e["walk_steps"] is None


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
