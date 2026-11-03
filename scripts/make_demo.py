#!/usr/bin/env python3
"""Fill a throwaway lifeos data directory with made-up data, for screenshots or a first look.

    LIFEOS_HOME=/tmp/lifeos-demo .venv/bin/python scripts/make_demo.py
    LIFEOS_HOME=/tmp/lifeos-demo .venv/bin/lifeos serve --port 8780
    # then open http://127.0.0.1:8780/#token=<printed token>

Everything here is invented. Refuses to touch a data directory that already has records.
"""
import random
from datetime import date, datetime, time, timedelta

from lifeos import auth, foods, jobs, profile, records, series
from lifeos.app import bootstrap
from lifeos.config import from_env
from lifeos.db import tx
from lifeos.samples import INSERT_SQL, ensure_metric, row_for
from lifeos.timeutil import iso, now, utc_now

DAYS = 120
WATCH = "apple_health:Apple Watch"
rng = random.Random(7)
smoke_rng = random.Random(11)  # its own stream, so the rest of the demo stays as it was

# name, amount, unit, grams, kcal, protein, fat, carbs
MENU = {
    "breakfast": [("鲑鱼饭团", 1, "个", 110, 180, 4.9, 1.6, 37.4), ("拿铁", 1, "杯", 300, 120, 6, 5, 10),
                  ("燕麦粥", 1, "碗", 250, 220, 8, 5, 36), ("鸡蛋三明治", 1, "个", 150, 320, 13, 15, 32),
                  ("酸奶", 1, "杯", 150, 95, 5, 3, 12)],
    "lunch": [("牛肉面", 1, "碗", 550, 620, 30, 16, 88), ("咖喱饭", 1, "盘", 450, 750, 18, 24, 110),
              ("鸡肉沙拉", 1, "份", 300, 380, 32, 18, 20), ("寿司拼盘", 8, "个", 280, 450, 22, 8, 72),
              ("拉面", 1, "碗", 600, 680, 26, 22, 90)],
    "dinner": [("烤鲑鱼定食", 1, "份", 450, 640, 35, 20, 75), ("麻婆豆腐", 1, "份", 300, 380, 20, 26, 12),
               ("米饭", 1, "碗", 150, 234, 3.8, 0.5, 53), ("番茄炒蛋", 1, "份", 250, 260, 13, 18, 10),
               ("蔬菜汤", 1, "碗", 300, 90, 4, 3, 12), ("姜烧猪肉", 1, "份", 200, 420, 24, 28, 14)],
    "snack": [("香蕉", 1, "根", 120, 105, 1.3, 0.4, 27), ("坚果", 1, "小把", 30, 180, 6, 15, 6)],
}
MEAL_TIMES = {"breakfast": (8, 5), "lunch": (12, 35), "dinner": (19, 10), "snack": (15, 40)}
WORKOUTS = [("running", "户外跑步", 34, 5.2, 330, 152), ("walking", "户外步行", 46, 3.6, 190, 108),
            ("cycling", "户外骑行", 52, 15.4, 410, 131)]
MOODS = [(8, ["开心", "放松"]), (6, ["平静"]), (5, ["有点累"]), (7, ["专注"]), (4, ["焦虑"], "作业截止前")]
JOURNAL = ["下午去代代木公园散步，银杏开始黄了。", "第一次试着自己做了姜烧猪肉，比想象中简单。",
           "今天效率很高，把报告的初稿写完了。", "下雨，在家看了一部老电影。"]


def item(entry):
    name, amount, unit, grams, kcal, p, f, c = entry
    return {"name": name, "amount": amount, "unit": unit, "grams": grams, "kcal": kcal, "protein_g": p,
            "fat_g": f, "carbs_g": c, "estimated": True, "confidence": "medium"}


def at(day: date, h: int, m: int, tz) -> datetime:
    return datetime.combine(day, time(h % 24, m), tz) + timedelta(days=h // 24)


def main() -> None:
    settings = from_env()
    conn = bootstrap(settings)
    if conn.execute("SELECT COUNT(*) FROM records").fetchone()[0]:
        raise SystemExit(f"{settings.home} already has records; use an empty LIFEOS_HOME")
    profile.patch(conn, {"name": "示例", "height_cm": 170, "timezone": "Asia/Tokyo", "currency": "JPY",
                         "goals": {"kcal": 1800, "protein_g": 110, "fat_g": 65, "carbs_g": 250,
                                   "steps": 8000, "exercise_min": 30, "sleep_hours": 7.5,
                                   "cigarettes_max": 6}}, "demo")
    tz = profile.timezone_of(conn)
    today = now(tz)
    stamp = utc_now()
    cache: dict = {}
    rows: list[tuple] = []

    def sample(name, value, start, end=None, unit=None):
        end = end or start
        m = ensure_metric(conn, name, unit, cache=cache)
        rows.append(row_for(m, value, unit, iso(start), start.timestamp(), start.date().isoformat(),
                            iso(end), end.timestamp(), end.date().isoformat(), WATCH, None, "demo", stamp))

    with tx(conn):
        for back in range(DAYS, -1, -1):
            day = today.date() - timedelta(days=back)
            last_hour = today.hour if back == 0 else 23
            fitness = 1 - back / DAYS * 0.25  # slowly getting fitter
            weekend = day.weekday() >= 5
            # sleep that ended this morning
            bed = datetime.combine(day - timedelta(days=1), time(23, rng.randint(0, 50)), tz)
            wake = at(day, 6, rng.randint(40, 59), tz)
            if weekend:
                wake += timedelta(minutes=rng.randint(30, 90))
            asleep = (wake - bed).total_seconds() / 60 * rng.uniform(0.9, 0.96)
            for stage, share in (("sleep_core", 0.53), ("sleep_deep", 0.15), ("sleep_rem", 0.24),
                                 ("sleep_awake", 0.04)):
                sample(stage, round(asleep * share * rng.uniform(0.85, 1.15)), bed, wake, "min")
            # the day's workout, every other day or so
            workout = WORKOUTS[back % 3] if back % 2 == 0 else None
            w_start = at(day, 7 if back % 4 == 0 else 18, 30, tz)
            if back == 0:
                workout, w_start = WORKOUTS[0], at(day, 7, 0, tz)
            active_total, exercise = 0.0, 0
            for h in range(7, min(23, last_hour + 1)):
                start = at(day, h, 0, tz)
                steps = rng.randint(150, 900) * (1.3 if weekend else 1.0) * fitness
                if workout and start <= w_start < start + timedelta(hours=1):
                    steps += workout[3] * 1300 if workout[0] != "cycling" else 0
                active = steps * 0.045 + rng.uniform(2, 8)
                active_total += active
                sample("step_count", round(steps), start, start + timedelta(minutes=59), "count")
                sample("active_energy", round(active, 1), start, start + timedelta(minutes=59), "kcal")
                sample("basal_energy", round(rng.uniform(68, 72), 1), start, start + timedelta(minutes=59), "kcal")
                sample("heart_rate", round(rng.uniform(66, 84) - 4 * fitness), start, unit="count/min")
                if h < 19 and rng.random() < 0.8:
                    sample("stand_hour", 1, start, start + timedelta(minutes=59), "count")
            for h in (*range(7), 23):  # resting burn goes on through the night too
                start = at(day, h, 0, tz)
                if start <= today:
                    sample("basal_energy", 64.0, start, start + timedelta(minutes=59), "kcal")
            sample("resting_heart_rate", round(rng.uniform(56, 60) - 3 * fitness), at(day, 7, 0, tz),
                   unit="count/min")
            sample("heart_rate_variability", round(rng.uniform(42, 55) + 6 * fitness, 1), at(day, 3, 0, tz),
                   unit="ms")
            if back % 2 == 0:
                sample("body_mass", round(68.6 - (DAYS - back) * 0.012 + rng.uniform(-0.3, 0.3), 1),
                       at(day, 7, 15, tz), unit="kg")
            for k in range(rng.randint(3, 5)):
                sample("dietary_water", rng.choice([250, 300, 500]), at(day, 9 + k * 3, 0, tz), unit="mL")
            if workout and w_start <= today:
                kind, name, minutes, km, kcal, hr = workout
                minutes = round(minutes * rng.uniform(0.9, 1.1))
                end = w_start + timedelta(minutes=minutes)
                exercise += minutes
                curve = [[iso(w_start + timedelta(minutes=i)),
                          hr - 14 + min(i, 8) * 2 - 2 + rng.randint(-2, 2) - 3,
                          hr - 14 + min(i, 8) * 2 + rng.randint(-2, 2),
                          hr - 14 + min(i, 8) * 2 + 3 + rng.randint(-2, 2)] for i in range(minutes)]
                recovery = [[iso(end + timedelta(seconds=s)), None, round(curve[-1][2] - s * 0.22), None]
                            for s in range(10, 121, 10)]
                for p in recovery:
                    p[1], p[3] = p[2] - 1, p[2] + 1
                avg = round(sum(p[2] for p in curve) / len(curve))
                rec, _, _ = records.upsert(conn, {
                    "category": "workout", "start": iso(w_start), "end": iso(end), "source": "apple_health",
                    "external_id": f"demo-workout-{day}",
                    "data": {"activity_type": kind, "activity_name": name, "duration_min": minutes,
                             "distance_km": round(km * rng.uniform(0.9, 1.1), 2),
                             "active_kcal": round(kcal * rng.uniform(0.9, 1.1)), "avg_hr": avg,
                             "max_hr": max(p[3] for p in curve), "min_hr": min(p[1] for p in curve),
                             "hr_recovery_1min": round(curve[-1][2] - recovery[5][2]),
                             "hr_recovery_2min": round(curve[-1][2] - recovery[-1][2])}}, "demo", tz, log=False)
                series.upsert(conn, rec["id"], "heart_rate", "count/min", "range", curve, "demo")
                series.upsert(conn, rec["id"], "heart_rate_recovery", "count/min", "range", recovery, "demo")
            sample("exercise_time", exercise + rng.randint(5, 15), at(day, 12, 0, tz), unit="min")
            records.upsert(conn, {
                "category": "activity_summary", "start": day.isoformat(), "local_date": day.isoformat(),
                "source": "apple_health", "external_id": f"activity:{day}",
                "data": {"move_kcal": round(active_total), "move_goal_kcal": 450, "exercise_min": exercise + 10,
                         "exercise_goal_min": 30, "stand_hours": rng.randint(9, 13), "stand_goal_hours": 12}},
                "demo", tz, log=False)
            for meal, (h, m) in MEAL_TIMES.items():
                when = at(day, h, m, tz)
                if when > today or (meal == "snack" and rng.random() < 0.6):
                    continue
                count = {"breakfast": 2, "lunch": 1, "dinner": 3, "snack": 1}[meal]
                picks = rng.sample(MENU[meal], count)
                records.upsert(conn, {"category": "meal", "start": iso(when), "source": "agent",
                                      "external_id": f"demo-{meal}-{day}",
                                      "data": {"meal_type": meal, "items": [item(p) for p in picks],
                                               "estimated": True}}, "claude-phone", tz, log=False)
                if meal == "lunch":
                    records.upsert(conn, {"category": "expense", "start": iso(when + timedelta(minutes=40)),
                                          "source": "agent", "external_id": f"demo-expense-{day}",
                                          "data": {"amount": rng.choice([880, 980, 1150, 1280]),
                                                   "currency": "JPY", "merchant": picks[0][0] + "店",
                                                   "kind": "food"}}, "claude-phone", tz, log=False)
            if at(day, 8, 30, tz) <= today:
                records.upsert(conn, {"category": "medication", "start": iso(at(day, 8, 30, tz)),
                                      "source": "agent", "external_id": f"demo-vitd-{day}",
                                      "data": {"name": "维生素D", "dose": 1000, "unit": "IU",
                                               "kind": "supplement"}}, "claude-phone", tz, log=False)
            if back % 3 == 0 and at(day, 21, 0, tz) <= today:
                score, emotions, *note = MOODS[back % len(MOODS)]
                records.upsert(conn, {"category": "mood", "start": iso(at(day, 21, 0, tz)), "source": "agent",
                                      "external_id": f"demo-mood-{day}", "note": note[0] if note else "",
                                      "data": {"score": score, "emotions": emotions}}, "claude-phone", tz,
                               log=False)
            if back % 7 == 1:
                records.upsert(conn, {"category": "journal", "start": iso(at(day, 22, 10, tz)), "source": "agent",
                                      "external_id": f"demo-journal-{day}",
                                      "data": {"text": JOURNAL[back % len(JOURNAL)]}}, "claude-phone", tz,
                               log=False)
            for k in range(max(0, smoke_rng.randint(5, 10) - (DAYS - back) // 30)):  # slowly cutting down
                when = at(day, smoke_rng.randint(8, 22), smoke_rng.randint(0, 59), tz)
                trigger = smoke_rng.choice(["饭后", "压力大", "和朋友", None, None, None])
                if when <= today:
                    records.upsert(conn, {"category": "smoking", "start": iso(when), "source": "manual",
                                          "external_id": f"demo-smoke-{day}-{k}",
                                          "data": {"count": 1, **({"trigger": trigger} if trigger else {})}},
                                   "web", tz, log=False)
        conn.executemany(INSERT_SQL, rows)
    for name, amount, unit, grams, kcal, p, fat, c in [MENU["lunch"][0], MENU["breakfast"][0], MENU["dinner"][2]]:
        foods.create(conn, {"name": name, "serving": {"amount": amount, "unit": unit, "grams": grams},
                            "per_serving": {"kcal": kcal, "protein_g": p, "fat_g": fat, "carbs_g": c},
                            "source": "estimate"}, "claude-phone")
    jobs.record(conn, "health_auto_export", None, "iphone-health", "done",
                {"samples_read": 48, "samples_added": 12, "samples_updated": 36})
    for token_name in ("claude-phone", "iphone-health"):
        auth.create(conn, token_name, "write")
    token = auth.create(conn, "demo", "admin")
    print(f"{len(rows)} samples, {conn.execute('SELECT COUNT(*) FROM records').fetchone()[0]} records in "
          f"{settings.home}\nopen http://127.0.0.1:<port>/#token={token}")


if __name__ == "__main__":
    main()
