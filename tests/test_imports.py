import zipfile

EXPORT_XML = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE HealthData [
<!ELEMENT HealthData (ExportDate,Me,(Record|Correlation|Workout|ActivitySummary)*)>
<!ATTLIST HealthData locale CDATA #REQUIRED>
]>
<HealthData locale="zh_CN">
 <ExportDate value="2026-11-02 08:00:00 +0900"/>
 <Me HKCharacteristicTypeIdentifierDateOfBirth="2003-11-03"
     HKCharacteristicTypeIdentifierBiologicalSex="HKBiologicalSexFemale"
     HKCharacteristicTypeIdentifierBloodType="HKBloodTypeNotSet"/>
 <Record type="HKQuantityTypeIdentifierStepCount" sourceName="iPhone" unit="count"
   creationDate="2026-11-01 09:10:00 +0900" startDate="2026-11-01 09:00:00 +0900"
   endDate="2026-11-01 09:10:00 +0900" value="800"/>
 <Record type="HKQuantityTypeIdentifierStepCount" sourceName="Apple Watch" unit="count"
   startDate="2026-11-01 09:01:00 +0900" endDate="2026-11-01 09:11:00 +0900" value="900">
  <MetadataEntry key="HKWasUserEntered" value="0"/>
 </Record>
 <Record type="HKQuantityTypeIdentifierBodyMass" sourceName="Health" unit="kg"
   startDate="2026-11-01 07:00:00 +0900" endDate="2026-11-01 07:00:00 +0900" value="55.2"/>
 <Record type="HKQuantityTypeIdentifierOxygenSaturation" sourceName="Apple Watch" unit="%"
   startDate="2026-11-01 03:00:00 +0900" endDate="2026-11-01 03:00:00 +0900" value="0.97"/>
 <Record type="HKQuantityTypeIdentifierNewThing" sourceName="App" unit="count"
   startDate="2026-11-01 03:00:00 +0900" endDate="2026-11-01 03:00:00 +0900" value="3"/>
 <Record type="HKCategoryTypeIdentifierSleepAnalysis" sourceName="Apple Watch"
   startDate="2026-10-31 23:30:00 +0900" endDate="2026-11-01 03:30:00 +0900"
   value="HKCategoryValueSleepAnalysisAsleepCore"/>
 <Record type="HKCategoryTypeIdentifierSleepAnalysis" sourceName="Apple Watch"
   startDate="2026-11-01 03:30:00 +0900" endDate="2026-11-01 04:30:00 +0900"
   value="HKCategoryValueSleepAnalysisAsleepDeep"/>
 <Record type="HKCategoryTypeIdentifierAppleStandHour" sourceName="Apple Watch"
   startDate="2026-11-01 10:00:00 +0900" endDate="2026-11-01 11:00:00 +0900"
   value="HKCategoryValueAppleStandHourStood"/>
 <Record type="HKCategoryTypeIdentifierHighHeartRateEvent" sourceName="Apple Watch"
   startDate="2026-11-01 10:00:00 +0900" endDate="2026-11-01 10:10:00 +0900"
   value="HKCategoryValueNotApplicable"/>
 <Correlation type="HKCorrelationTypeIdentifierBloodPressure" sourceName="Omron"
   startDate="2026-11-01 08:00:00 +0900" endDate="2026-11-01 08:00:00 +0900">
  <Record type="HKQuantityTypeIdentifierBloodPressureSystolic" sourceName="Omron" unit="mmHg"
    startDate="2026-11-01 08:00:00 +0900" endDate="2026-11-01 08:00:00 +0900" value="112"/>
  <Record type="HKQuantityTypeIdentifierBloodPressureDiastolic" sourceName="Omron" unit="mmHg"
    startDate="2026-11-01 08:00:00 +0900" endDate="2026-11-01 08:00:00 +0900" value="72"/>
 </Correlation>
 <Workout workoutActivityType="HKWorkoutActivityTypeRunning" duration="30.5" durationUnit="min"
   sourceName="Apple Watch" startDate="2026-11-01 07:00:00 +0900"
   endDate="2026-11-01 07:30:30 +0900">
  <MetadataEntry key="HKIndoorWorkout" value="0"/>
  <MetadataEntry key="HKElevationAscended" value="2300 cm"/>
  <WorkoutStatistics type="HKQuantityTypeIdentifierActiveEnergyBurned" sum="301.5" unit="kcal"/>
  <WorkoutStatistics type="HKQuantityTypeIdentifierDistanceWalkingRunning" sum="5.02" unit="km"/>
  <WorkoutStatistics type="HKQuantityTypeIdentifierHeartRate" average="151.2" minimum="98"
    maximum="171" unit="count/min"/>
  <WorkoutStatistics type="HKQuantityTypeIdentifierRunningPower" average="210" unit="W"/>
  <WorkoutRoute sourceName="Apple Watch">
   <FileReference path="/workout-routes/route_2026-11-01_7.30am.gpx"/>
  </WorkoutRoute>
 </Workout>
 <ActivitySummary dateComponents="2026-11-01" activeEnergyBurned="512.3"
   activeEnergyBurnedGoal="450" activeEnergyBurnedUnit="kcal" appleMoveTime="0"
   appleMoveTimeGoal="0" appleExerciseTime="41" appleExerciseTimeGoal="30"
   appleStandHours="11" appleStandHoursGoal="12"/>
 <ActivitySummary dateComponents="2020-01-01" activeEnergyBurned="0"
   activeEnergyBurnedGoal="0" appleExerciseTime="0" appleStandHours="0"/>
</HealthData>
"""
GPX = b"<gpx><trk><trkseg><trkpt lat='35.6' lon='139.7'/></trkseg></trk></gpx>"


def make_zip(path):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("apple_health_export/导出.xml", EXPORT_XML)
        z.writestr("apple_health_export/导出_cda.xml", "<ClinicalDocument/>")
        z.writestr("apple_health_export/workout-routes/route_2026-11-01_7.30am.gpx", GPX)
    return path


def run_import(client, path):
    r = client.post("/api/v1/imports/apple-health", content=path.read_bytes(),
                    headers={"Content-Type": "application/zip"})
    assert r.status_code == 202, r.text
    import time
    for _ in range(100):
        job = client.get(f"/api/v1/imports/{r.json()['import_id']}").json()
        if job["status"] in ("done", "failed"):
            return job
        time.sleep(0.05)
    raise AssertionError("import did not finish")


def test_apple_health_export(client, tmp_path):
    job = run_import(client, make_zip(tmp_path / "export.zip"))
    assert job["status"] == "done", job
    stats = job["stats"]
    assert stats["workouts"] == 1 and stats["activity_days"] == 1 and stats["routes"] == 1
    assert stats["samples_added"] == 11

    day = client.get("/api/v1/summary/day", params={"date": "2026-11-01"}).json()
    assert day["activity"]["steps"] == 900          # iPhone + Watch same hour: counted once
    assert day["activity"]["stand_hours"] == 1
    assert day["activity"]["spo2_pct"] == 97        # fraction → percent
    assert day["sleep"]["asleep_min"] == 300        # dated by wake-up day
    assert day["rings"]["move_kcal"] == 512.3
    assert day["body"] == {"weight_kg": 55.2, "weight_date": "2026-11-01"}
    w = day["workouts"][0]
    assert w["activity_type"] == "running" and w["distance_km"] == 5.02
    workout = client.get(f"/api/v1/records/{w['id']}").json()
    assert workout["data"]["avg_hr"] == 151.2 and workout["data"]["elevation_up_m"] == 23
    assert workout["data"]["indoor"] is False
    assert workout["data"]["other_stats"]["running_power"]["average"] == 210
    assert workout["attachments"][0]["filename"] == "route_2026-11-01_7.30am.gpx"
    att = client.get(f"/api/v1/attachments/{workout['attachments'][0]['id']}")
    assert att.content == GPX

    metrics = {m["name"]: m for m in client.get("/api/v1/metrics").json()["metrics"]}
    assert metrics["new_thing"]["last_date"] == "2026-11-01"
    assert metrics["high_heart_rate_event"]["aggregation"] == "sum"
    bp = client.get("/api/v1/samples", params={"metric": "blood_pressure_systolic"}).json()
    assert bp["samples"][0]["value"] == 112
    prof = client.get("/api/v1/profile").json()["profile"]
    assert prof["birthday"] == "2003-11-03" and prof["sex"] == "female"
    assert "blood_type" not in prof

    # Importing the same export again adds nothing and keeps one workout and one route.
    again = run_import(client, make_zip(tmp_path / "export2.zip"))
    assert again["stats"]["samples_added"] == 0 and again["stats"]["routes"] == 0
    assert len(client.get("/api/v1/records", params={"category": "workout"}).json()
               ["records"]) == 1


def test_profile_import_does_not_override_user(client, tmp_path):
    client.patch("/api/v1/profile", json={"sex": "nonbinary"})
    run_import(client, make_zip(tmp_path / "export.zip"))
    assert client.get("/api/v1/profile").json()["profile"]["sex"] == "nonbinary"


HAE = {"data": {
    "metrics": [
        {"name": "step_count", "units": "count", "data": [
            {"qty": 1500, "date": "2026-11-02 09:00:00 +0900", "source": "Apple Watch"}]},
        {"name": "heart_rate", "units": "count/min", "data": [
            {"date": "2026-11-02 09:00:00 +0900", "Min": 60, "Avg": 72, "Max": 95,
             "source": "Apple Watch"}]},
        {"name": "weight_body_mass", "units": "kg", "data": [
            {"qty": 55.0, "date": "2026-11-02 07:00:00 +0900"}]},
        {"name": "blood_pressure", "units": "mmHg", "data": [
            {"date": "2026-11-02 08:00:00 +0900", "systolic": 118, "diastolic": 76}]},
        {"name": "sleep_analysis", "units": "hr", "data": [
            {"date": "2026-11-02 00:00:00 +0900", "totalSleep": 7.0, "asleep": 0, "core": 4.0,
             "deep": 1.5, "rem": 1.5, "awake": 0.25, "inBed": 7.5,
             "sleepStart": "2026-11-01 23:30:00 +0900", "sleepEnd": "2026-11-02 06:45:00 +0900",
             "inBedStart": "2026-11-01 23:15:00 +0900", "inBedEnd": "2026-11-02 06:45:00 +0900",
             "source": "Apple Watch"}]},
        {"name": "dietary_energy", "units": "kJ", "data": [
            {"qty": 418.4, "date": "2026-11-02 12:00:00 +0900", "foodType": "Rice"}]},
    ],
    "workouts": [{
        "id": "550e8400", "name": "Outdoor Walk", "start": "2026-11-02 18:00:00 +0900",
        "end": "2026-11-02 18:40:00 +0900", "duration": 2400,
        "activeEnergyBurned": {"qty": 150, "units": "kcal"},
        "distance": {"qty": 2.0, "units": "mi"},
        "heartRate": {"avg": {"qty": 110, "units": "count/min"},
                      "max": {"qty": 130, "units": "count/min"}},
        "route": [{"latitude": 35.66, "longitude": 139.70, "altitude": 30,
                   "timestamp": "2026-11-02 18:00:00 +0900"}],
    }],
    "stateOfMind": [{"id": "x"}],
}}


def test_health_auto_export(client, settings):
    r = client.post("/api/v1/imports/health-auto-export", json=HAE,
                    headers={"session-id": "abc", "automation-name": "lifeos"})
    assert r.status_code == 200, r.text
    stats = r.json()
    assert stats["workouts"] == 1 and stats["routes"] == 1
    assert stats["raw_only"] == {"stateOfMind": 1} and stats["unparsed_points"] == 0
    raw = list((settings.raw_dir / "health-auto-export").rglob("*-abc.json"))
    assert len(raw) == 1

    day = client.get("/api/v1/summary/day", params={"date": "2026-11-02"}).json()
    assert day["activity"]["steps"] == 1500 and day["activity"]["avg_hr"] == 72
    assert day["sleep"]["asleep_hours"] == 7.0 and day["sleep"]["in_bed_min"] == 450
    assert day["body"]["weight_kg"] == 55.0
    w = client.get(f"/api/v1/records/{day['workouts'][0]['id']}").json()
    assert w["data"]["activity_type"] == "walking" and w["data"]["distance_km"] == 3.22
    assert w["data"]["duration_min"] == 40 and w["data"]["avg_hr"] == 110
    assert w["attachments"][0]["mime"] == "application/gpx+xml"
    kcal = client.get("/api/v1/samples", params={"metric": "dietary_energy"}).json()
    assert kcal["samples"][0]["value"] == 100 and kcal["samples"][0]["meta"] == {
        "foodType": "Rice"}

    # The same payload again: no new samples, the workout is updated in place.
    again = client.post("/api/v1/imports/health-auto-export", json=HAE).json()
    assert again["samples_added"] == 0 and again["routes"] == 0
    assert len(client.get("/api/v1/records", params={"category": "workout"}).json()
               ["records"]) == 1


def test_workout_from_both_importers_is_one_record(client, tmp_path):
    run_import(client, make_zip(tmp_path / "export.zip"))
    hae = {"data": {"workouts": [{"name": "Outdoor Run", "start": "2026-11-01 07:00:00 +0900",
                                  "end": "2026-11-01 07:30:30 +0900", "duration": 1830}]}}
    client.post("/api/v1/imports/health-auto-export", json=hae)
    workouts = client.get("/api/v1/records", params={"category": "workout"}).json()["records"]
    assert len(workouts) == 1


def test_reimport_keeps_user_edits_and_deletes(client):
    hae = {"data": {"workouts": [{"name": "Outdoor Run", "start": "2026-11-02 07:00:00 +0900",
                                  "end": "2026-11-02 07:30:00 +0900", "duration": 1800,
                                  "activeEnergyBurned": {"qty": 280, "units": "kcal"}}]}}
    client.post("/api/v1/imports/health-auto-export", json=hae)
    w = client.get("/api/v1/records", params={"category": "workout"}).json()["records"][0]
    client.patch(f"/api/v1/records/{w['id']}", json={"data": {"rpe": 8}, "note": "腿很酸"})
    hae["data"]["workouts"][0]["activeEnergyBurned"]["qty"] = 300
    client.post("/api/v1/imports/health-auto-export", json=hae)
    after = client.get(f"/api/v1/records/{w['id']}").json()
    assert after["data"]["rpe"] == 8 and after["note"] == "腿很酸"
    assert after["data"]["active_kcal"] == 300
    client.delete(f"/api/v1/records/{w['id']}")
    client.post("/api/v1/imports/health-auto-export", json=hae)
    assert client.get(f"/api/v1/records/{w['id']}").json()["deleted_at"] is not None


def test_resent_period_replaces_partial_values(client):
    def payload(steps, core_hours, sleep_end):
        return {"data": {"metrics": [
            {"name": "step_count", "units": "count", "data": [
                {"qty": steps, "date": "2026-11-02 09:00:00 +0900", "source": "Apple Watch"}]},
            {"name": "sleep_analysis", "units": "hr", "data": [
                {"date": "2026-11-02 00:00:00 +0900", "core": core_hours,
                 "sleepStart": "2026-11-01 23:30:00 +0900", "sleepEnd": sleep_end,
                 "source": "Apple Watch"}]}]}}
    first = client.post("/api/v1/imports/health-auto-export",
                        json=payload(300, 2.0, "2026-11-02 03:00:00 +0900")).json()
    assert first["samples_added"] == 2 and first["samples_updated"] == 0
    second = client.post("/api/v1/imports/health-auto-export",
                         json=payload(800, 4.0, "2026-11-02 06:45:00 +0900")).json()
    assert second["samples_added"] == 0 and second["samples_updated"] == 2
    day = client.get("/api/v1/summary/day", params={"date": "2026-11-02"}).json()
    assert day["activity"]["steps"] == 800         # not 300 + 800
    assert day["sleep"]["core_min"] == 240           # not 120 + 240


def _workout_with_metrics(hr_end=150):
    t = "2026-11-03 07:{:02d}:00 +0900"
    return {"data": {"workouts": [{
        "id": "w-1", "name": "Outdoor Run", "start": t.format(0), "end": t.format(30),
        "duration": 1800, "location": "Outdoor", "isIndoor": False,
        "activeEnergyBurned": {"qty": 300, "units": "kcal"},
        "distance": {"qty": 3.1, "units": "mi"},
        "avgSpeed": {"qty": 6.2, "units": "mi/hr"},
        "temperature": {"qty": 68, "units": "degF"},
        "intensity": {"qty": 8.5, "units": "kcal/hr·kg"},
        "effortScore": {"qty": 6, "units": "appleEffortScore"},
        "stepCadence": {"qty": 170, "units": "count/min"},
        "flightsClimbed": {"qty": 3, "units": "count"},
        "lapLength": {"qty": 0.25, "units": "mi"},
        "weirdThing": {"qty": 1, "units": "zz"},
        "strokeStyle": "Unknown",
        "metadata": {"HKAverageMETs": "8.1"},
        "heartRateData": [
            {"date": t.format(0), "Min": 90, "Avg": 110, "Max": 120, "units": "count/min"},
            {"date": t.format(29), "Min": 140, "Avg": hr_end, "Max": 160, "units": "count/min"}],
        "heartRateRecovery": [
            {"date": t.format(31), "Min": 120, "Avg": 125, "Max": 130, "units": "count/min"},
            {"date": t.format(32), "Min": 100, "Avg": 105, "Max": 110, "units": "count/min"}],
        "activeEnergy": [{"date": t.format(0), "qty": 41.84, "units": "kJ"},
                         {"date": t.format(1), "qty": 12, "units": "kcal"}],
        "walkingAndRunningDistance": [{"date": t.format(0), "qty": 0.5, "units": "mi"}],
        "stepCount": [{"date": t.format(0), "qty": 2500, "units": "count"},
                      {"date": t.format(15), "qty": 2600, "units": "count"}],
        "laps": [{"index": 1, "duration": 600}, {"index": 2, "duration": 610}],
    }]}}


def test_workout_metrics(client):
    r = client.post("/api/v1/imports/health-auto-export", json=_workout_with_metrics()).json()
    assert r["workouts"] == 1 and r["series"] == 6 and r["samples_added"] == 0
    rec = client.get("/api/v1/records", params={"category": "workout"}).json()["records"][0]
    d = rec["data"]
    assert d["distance_km"] == 4.99 and d["avg_speed_kmh"] == 9.98
    assert d["temperature_c"] == 20 and d["intensity"] == 8.5 and d["effort_score"] == 6
    assert d["step_cadence"] == 170 and d["flights_climbed"] == 3 and d["lap_length_m"] == 402.34
    assert d["avg_hr"] == 130 and d["max_hr"] == 160 and d["min_hr"] == 90
    assert d["hr_recovery_1min"] == 25 and d["hr_recovery_2min"] == 45
    assert d["step_count"] == 5100
    assert d["other_stats"] == {"weird_thing": {"value": 1, "unit": "zz"},
                                "stroke_style": "Unknown"}
    assert d["metadata"] == {"HKAverageMETs": "8.1"}

    full = client.get(f"/api/v1/records/{rec['id']}").json()
    assert {s["name"] for s in full["series"]} == {
        "heart_rate", "heart_rate_recovery", "active_energy", "walking_running_distance",
        "step_count", "laps"}
    s = client.get(f"/api/v1/records/{rec['id']}/series").json()["series"]
    by = {x["name"]: x for x in s}
    assert by["heart_rate"]["kind"] == "range" and by["heart_rate"]["stats"]["max"] == 160
    assert by["active_energy"]["points"] == [["2026-11-03T07:00:00+09:00", 10.0],
                                             ["2026-11-03T07:01:00+09:00", 12]]
    assert by["walking_running_distance"]["unit"] == "km"
    assert by["walking_running_distance"]["points"][0][1] == 0.805
    assert by["laps"]["kind"] == "raw" and by["laps"]["points"][1]["duration"] == 610
    only = client.get(f"/api/v1/records/{rec['id']}/series",
                      params={"name": "heart_rate", "points": False}).json()["series"]
    assert len(only) == 1 and "points" not in only[0]

    # Workout curves never leak into day totals.
    day = client.get("/api/v1/summary/day", params={"date": "2026-11-03"}).json()
    assert "steps" not in day["activity"] and "active_kcal" not in day["activity"]

    # A re-sent workout replaces its curves.
    r2 = client.post("/api/v1/imports/health-auto-export",
                     json=_workout_with_metrics(hr_end=170)).json()
    assert r2["workouts"] == 1
    hr = client.get(f"/api/v1/records/{rec['id']}/series",
                    params={"name": "heart_rate"}).json()["series"][0]
    assert hr["points"][-1][2] == 170
    after = client.get(f"/api/v1/records/{rec['id']}").json()["data"]
    assert after["hr_recovery_1min"] == 45


def test_export_workout_gets_heart_rate_from_samples(client, tmp_path):
    run_import(client, make_zip(tmp_path / "export.zip"))
    client.post("/api/v1/samples", json={"samples": [
        {"metric": "heart_rate", "value": v, "start": f"2026-11-01T07:{m:02d}:00+09:00",
         "source": "apple_health:Apple Watch"} for m, v in ((5, 140), (20, 160), (45, 90))]})
    w = client.get("/api/v1/records", params={"category": "workout"}).json()["records"][0]
    s = client.get(f"/api/v1/records/{w['id']}/series").json()["series"]
    assert s[0]["name"] == "heart_rate" and s[0]["source"] == "samples"
    assert [p[1] for p in s[0]["points"]] == [140, 160]   # 07:45 is after the workout


def test_migrates_v1_database(tmp_path):
    from lifeos.db import SCHEMA, connect, migrate
    conn = connect(tmp_path / "old.db")
    conn.executescript(SCHEMA)
    conn.execute("PRAGMA user_version=1")
    migrate(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
    conn.execute("SELECT * FROM record_series").fetchall()


def test_localized_payload_like_a_chinese_iphone(client):
    t = "2026-11-02 16:{:02d}:00 +0900"
    w = {"name": "室内 步行", "location": "室内", "isIndoor": True,
         "start": t.format(9), "end": t.format(23), "duration": 788,
         "activeEnergyBurned": {"units": "kJ", "qty": 139.94},
         "avgHeartRate": {"units": "bpm", "qty": 84.02},
         "heartRate": {"avg": {"qty": 84.02, "units": "bpm"}, "max": {"qty": 103, "units": "bpm"}},
         "stepCount": [{"qty": 79.2, "date": t.format(9), "source": "", "units": "步"},
                       {"qty": 80.8, "date": t.format(10), "source": "", "units": "步"}]}
    metrics = [{"name": "step_count", "units": "count", "data": [
        {"source": "", "date": "2026-11-02 09:00:00 +0900", "qty": 1234.4},
        {"source": "", "date": "2026-11-02 10:00:00 +0900", "qty": 100.2}]},
        {"name": "walking_step_length", "units": "cm", "data": [
            {"qty": 53.5, "date": "2026-11-02 12:00:00 +0900", "source": "iPhone"}]},
        {"name": "number_of_times_fallen", "units": "count", "data": [
            {"qty": 1, "date": "2026-11-02 12:00:00 +0900", "source": "Watch"}]}]
    client.post("/api/v1/imports/health-auto-export",
                json={"data": {"workouts": [w], "metrics": metrics}})
    rec = client.get("/api/v1/records", params={"category": "workout"}).json()["records"][0]
    assert rec["data"]["activity_type"] == "walking" and rec["data"]["active_kcal"] == 33.45
    assert rec["data"]["avg_hr"] == 84.02 and rec["data"]["step_count"] == 160
    steps = client.get(f"/api/v1/records/{rec['id']}/series",
                       params={"name": "step_count"}).json()["series"][0]
    assert steps["unit"] == "count"
    day = client.get("/api/v1/summary/day", params={"date": "2026-11-02"}).json()
    assert day["activity"]["steps"] == 1335
    metrics_by = {m["name"]: m for m in client.get("/api/v1/metrics").json()["metrics"]}
    assert metrics_by["walking_step_length"]["aggregation"] == "avg"
    assert metrics_by["number_of_times_fallen"]["aggregation"] == "sum"


def test_activity_type_names():
    from lifeos.importers import activity_type
    assert activity_type("室外 跑步") == "running"
    assert activity_type("功能性力量训练") == "functional_strength_training"
    assert activity_type("Outdoor Walk") == "walking"
    assert activity_type("某种新运动") == "other"
