"""Writes an agent makes under its own names: renamed when it is clear what was meant, refused
when it is not, so nothing is stored and then left out of what the user sees."""

DAY = "2026-11-02"
RUN = {"category": "workout", "start": f"{DAY}T07:00:00+09:00", "end": f"{DAY}T07:35:00+09:00",
       "data": {"activity_type": "running"}}


def _goals(client, goals):
    return client.patch("/api/v1/profile", json={"goals": goals})


def test_goal_aliases_are_the_goals_that_are_read(client):
    _goals(client, {"kcal": 1500, "protein_g": 100, "cigarettes_max": 10})
    r = _goals(client, {"calories": 1400, "protein": 120, "max_cigarettes": 8, "Steps": 6000,
                        "target_weight": 57})
    assert r.status_code == 200, r.text
    assert r.json()["profile"]["goals"] == {"kcal": 1400, "protein_g": 120, "cigarettes_max": 8,
                                            "steps": 6000, "weight_kg": 57}
    day = client.get(f"/api/v1/summary/day?date={DAY}").json()
    assert day["nutrition"]["goals"] == {"kcal": 1400, "protein_g": 120}
    assert day["smoking"] is None or day["smoking"]["daily_max"] == 8
    # The key itself wins over an alias sent with it, and a null alias removes the goal.
    goals = _goals(client, {"Calories": 1300, "kcal": 1450}).json()["profile"]["goals"]
    assert goals["kcal"] == 1450
    goals = _goals(client, {"calories": None}).json()["profile"]["goals"]
    assert "kcal" not in goals


def test_unknown_goals_are_refused(client):
    _goals(client, {"kcal": 1500})
    for goals in ({"energy": 1400}, {"kcal": 1400, "water": 2}, {"stand_hours": 12}):
        r = _goals(client, goals)
        assert r.status_code == 400, goals
        err = r.json()["error"]
        assert "nothing reads it" in err["message"]
        assert "kcal, protein_g" in err["hint"] and "cigarettes_max" in err["hint"]
    assert client.get("/api/v1/profile").json()["profile"]["goals"] == {"kcal": 1500}


def test_a_goal_stored_under_an_unknown_name_can_be_removed(client, conn):
    _goals(client, {"kcal": 1500})
    conn.execute("UPDATE profile SET value = ? WHERE key = 'goals'", ('{"kcal": 1500, "junk": 3}',))
    conn.commit()
    r = _goals(client, {"junk": None})
    assert r.status_code == 200 and r.json()["profile"]["goals"] == {"kcal": 1500}


def test_workout_aliases_are_renamed(client):
    body = {**RUN, "end": None, "data": {"activity_type": "running", "calories": 330,
                                         "distance": 5.2, "Duration": 35, "avg_heart_rate": 150}}
    r = client.post("/api/v1/records", json=body)
    assert r.status_code == 201, r.text
    rec = r.json()["record"]
    assert rec["data"] == {"activity_type": "running", "active_kcal": 330, "distance_km": 5.2,
                           "duration_min": 35, "avg_hr": 150}
    assert rec["title"] == "运动：running · 5.2 km · 35 分钟"
    assert "read data.calories as active_kcal; send active_kcal next time" in r.json()["warnings"]
    w = client.get(f"/api/v1/summary/day?date={DAY}").json()["workouts"][0]
    assert (w["active_kcal"], w["distance_km"], w["duration_min"]) == (330, 5.2, 35)

    # A correction under the alias replaces the stored value instead of losing to it.
    r = client.patch(f"/api/v1/records/{rec['id']}", json={"data": {"kcal": 300}})
    assert r.json()["record"]["data"]["active_kcal"] == 300
    assert r.json()["warnings"] == ["read data.kcal as active_kcal; send active_kcal next time"]


def test_workout_alias_units_and_conflicts(client):
    swim = {**RUN, "end": None, "data": {"activity_type": "swimming", "distance_m": 1500,
                                         "duration_s": 2700, "active_kcal": 400, "kcal": 380}}
    r = client.post("/api/v1/records", json=swim)
    assert r.status_code == 201, r.text
    data = r.json()["record"]["data"]
    assert (data["distance_km"], data["duration_min"], data["active_kcal"]) == (1.5, 45, 400)
    assert "kcal" not in data
    assert "read data.distance_m as distance_km = 1.5; send distance_km next time" in r.json()["warnings"]
    assert "dropped data.kcal, active_kcal is already given" in r.json()["warnings"]
    # A bare distance this large is metres, not km: refused rather than stored as 1500 km.
    r = client.post("/api/v1/records", json={**swim, "data": {"activity_type": "swimming",
                                                              "distance": 1500}})
    assert r.status_code == 422 and "distance_m" in r.json()["error"]["hint"]
    # Under an alias, a string fails just as it would under the key.
    r = client.post("/api/v1/records", json={**RUN, "data": {"activity_type": "running",
                                                             "duration": "35 min"}})
    assert r.status_code == 422 and "['duration_min']" in r.json()["error"]["message"]


def test_workout_duration_follows_new_times(client):
    rec = client.post("/api/v1/records", json=RUN).json()["record"]
    assert rec["data"]["duration_min"] == 35 and rec["title"] == "运动：running · 35 分钟"
    url = f"/api/v1/records/{rec['id']}"
    after = client.patch(url, json={"end": f"{DAY}T07:50:00+09:00"}).json()["record"]
    assert after["data"]["duration_min"] == 50 and after["title"] == "运动：running · 50 分钟"
    # The same time written another way is no change; a note leaves the duration alone.
    after = client.patch(url, json={"start": f"{DAY}T07:00:00+09:00", "note": "轻松跑"}).json()
    assert after["record"]["data"]["duration_min"] == 50
    # A duration the caller sends with the new times is theirs (paused, say).
    after = client.patch(url, json={"end": f"{DAY}T08:00:00+09:00",
                                    "data": {"duration_min": 52}}).json()["record"]
    assert after["data"]["duration_min"] == 52
    # An external_id resend with new times recomputes it too.
    body = {**RUN, "source": "calendar", "external_id": "run-1"}
    client.post("/api/v1/records", json=body)
    again = client.post("/api/v1/records", json={**body, "end": f"{DAY}T07:45:00+09:00"}).json()
    assert again["record"]["data"]["duration_min"] == 45
    assert again["record"]["title"] == "运动：running · 45 分钟"


def test_workout_duration_without_an_end_survives_a_move(client):
    body = {**RUN, "end": None, "data": {"activity_type": "yoga", "duration_min": 30}}
    rec = client.post("/api/v1/records", json=body).json()["record"]
    after = client.patch(f"/api/v1/records/{rec['id']}",
                         json={"start": f"{DAY}T18:00:00+09:00"}).json()["record"]
    assert after["data"]["duration_min"] == 30 and after["end"] is None


def test_upsert_without_times_keeps_them(client):
    body = {**RUN, "source": "notion", "external_id": "page-1"}
    first = client.post("/api/v1/records", json=body).json()["record"]
    again = client.post("/api/v1/records", json={
        "category": "workout", "source": "notion", "external_id": "page-1",
        "data": {"activity_type": "running", "rpe": 6}})
    assert again.status_code == 200, again.text
    rec = again.json()["record"]
    assert (rec["start"], rec["end"], rec["local_date"]) == (first["start"], first["end"], DAY)
    assert rec["data"]["rpe"] == 6 and rec["data"]["duration_min"] == 35
    # An end alone moves only the end.
    rec = client.post("/api/v1/records", json={
        "category": "workout", "source": "notion", "external_id": "page-1",
        "end": f"{DAY}T07:40:00+09:00", "data": {"activity_type": "running"}}).json()["record"]
    assert rec["start"] == first["start"] and rec["data"]["duration_min"] == 40


def test_patch_cannot_remove_start(client):
    rec = client.post("/api/v1/records", json=RUN).json()["record"]
    r = client.patch(f"/api/v1/records/{rec['id']}", json={"start": None})
    assert r.status_code == 400
    assert client.get(f"/api/v1/records/{rec['id']}").json()["start"] == rec["start"]


def _post(client, *samples):
    return client.post("/api/v1/samples", json={"samples": [
        {"start": f"{DAY}T08:{i:02d}:00+09:00", **s} for i, s in enumerate(samples)]})


def test_sample_metric_aliases_go_to_the_metrics_that_are_read(client):
    r = _post(client, {"metric": "weight_kg", "value": 57.2}, {"metric": "steps", "value": 3000},
              {"metric": "water_ml", "value": 500}, {"metric": "body_weight", "value": 57.0},
              {"metric": "weight_kg", "value": 57.1})
    assert r.status_code == 200, r.text
    assert [s["metric"] for s in r.json()["samples"]] == [
        "body_mass", "step_count", "dietary_water", "body_mass", "body_mass"]
    assert r.json()["warnings"] == [
        "metric weight_kg is body_mass; send body_mass next time",
        "metric steps is step_count; send step_count next time",
        "metric water_ml is dietary_water; send dietary_water next time",
        "metric body_weight is body_mass; send body_mass next time"]
    names = {m["name"] for m in client.get("/api/v1/metrics").json()["metrics"]}
    assert not names & {"weight_kg", "steps", "water_ml", "body_weight"}
    day = client.get(f"/api/v1/summary/day?date={DAY}").json()
    assert day["body"]["weight_kg"] == 57.1
    assert day["activity"]["steps"] == 3000 and day["activity"]["water_ml"] == 500


def test_sample_units_are_converted(client):
    r = _post(client, {"metric": "body_mass", "value": 114, "unit": "斤"},
              {"metric": "body_mass", "value": 125, "unit": "lbs"},
              {"metric": "dietary_water", "value": 1.5, "unit": "l"},
              {"metric": "dietary_water", "value": 0.5, "unit": "Liter"},
              {"metric": "exercise_time", "value": 1.5, "unit": "h"},
              {"metric": "mindful_minutes", "value": 0.25, "unit": "hours"})
    assert r.status_code == 200, r.text
    assert [(s["value"], s["unit"]) for s in r.json()["samples"]] == [
        (57, "kg"), (56.699046, "kg"), (1500, "mL"), (500, "mL"), (90, "min"), (15, "min")]
    assert r.json()["warnings"] == []


def test_sample_units_that_cannot_be_converted_are_refused(client):
    for metric, unit in (("dietary_water", "杯"), ("water", "cups"), ("body_mass", "st")):
        r = _post(client, {"metric": "step_count", "value": 100}, {"metric": metric, "value": 2,
                                                                   "unit": unit})
        assert r.status_code == 400, (metric, unit)
        err = r.json()["error"]
        assert f"{unit!r} cannot be converted" in err["message"]
        assert err["hint"].startswith("send the value in one of " + ("mL" if "water" in metric else "kg"))
    assert client.get("/api/v1/samples").json()["samples"] == []  # nothing from those batches


def test_new_metrics_say_so_and_keep_their_unit(client):
    r = _post(client, {"metric": "pushups", "value": 20, "unit": "times"},
              {"metric": "coffee_cups", "value": 2, "unit": "杯"},
              {"metric": "coffee_cups", "value": 1, "unit": "杯"})
    assert r.status_code == 200, r.text
    assert [(s["metric"], s["unit"]) for s in r.json()["samples"]] == [
        ("pushups", "count"), ("coffee_cups", "杯"), ("coffee_cups", "杯")]
    assert r.json()["warnings"] == [
        "created metric pushups; if you meant an existing one, GET /metrics lists them",
        "created metric coffee_cups; if you meant an existing one, GET /metrics lists them"]
    again = _post(client, {"metric": "pushups", "value": 15, "start": f"{DAY}T21:00:00+09:00"})
    assert again.json()["warnings"] == []
