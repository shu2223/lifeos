# lifeos — agent manual

lifeos holds one person's life records on their own machine: what they eat, Apple Health and
Apple Fitness data, workouts, sleep, weight, mood, diary, medication, spending, smoking, and any
category an agent adds later. You (an agent) read and write it over HTTP on the owner's behalf.

**API root: `{BASE}/api/v1`.** This is the one address an agent needs, and every endpoint
lives under it. This manual is `{BASE}/api/v1/manual` and the OpenAPI spec is
`{BASE}/api/v1/openapi.json`. Paths outside the API root may sit behind the owner's own login,
so don't use them.

## 1. Rules

1. **Record what the user tells you, without making them fill in a form.** "我中午吃了牛肉面"
   should become a complete meal record with estimated calories and macros in one turn. Fill
   gaps with sensible estimates, write the assumptions into the record, and tell the user what
   you assumed so they can correct it. Ask only when a guess would be badly wrong (for example,
   "吃了点东西" with nothing else to go on).
2. **Never invent events.** Estimate quantities and nutrients; do not invent meals, workouts or
   feelings the user did not mention.
3. **Use the user's own words** in `name` fields and in `note` where it helps; their language
   is usually Chinese.
4. **Correct mistakes by changing records, not by adding new ones.** PATCH a wrong record, and
   DELETE a duplicate (deletes can be undone). Every write is attributed to your token and
   logged in `/changes`.
5. **Read before you answer questions** about the user's data. Do not answer from memory.
6. **After writing, confirm briefly**: what you recorded, the key numbers, and today's running
   total if it is relevant. For example: "记好了：午餐 牛肉面+煎蛋 ≈ 710 kcal，今天累计 1,240 / 2,000
   kcal".

## 2. Authentication

Send `Authorization: Bearer <token>` on every `/api/v1/...` call (`X-API-Key: <token>` also
works). Tokens are per agent, and each has a scope: `read`, `write` (includes read), or `admin`
(includes write and also manages tokens). `GET /api/v1/whoami` tells you which token you are
using.

## 3. Start of a session: one call

`GET /api/v1/context` returns everything you need before writing:

- `now`, `today` and `timezone`: use these, not your own clock.
- `profile`: name, height, goals, diet preferences and allergies.
- `categories`: what kinds of records exist.
- `metrics_with_data`: which numeric series exist.
- `today_summary`: meals so far, intake against goals, steps, sleep, workouts, cigarettes and
  the energy bar (`energy`, see §6.8).
- `frequent_foods`: the user's food library, most-used first.
- `recent_records`: the last 10 entries, which helps you avoid writing duplicates.

## 4. Data model: which kind of thing to write

| The user says… | Write | Endpoint |
|---|---|---|
| ate or drank something | record, category `meal` | `POST /records` |
| a workout ("跑了5公里", "练了腿") | record, category `workout` | `POST /records` |
| a feeling ("今天很开心", "压力大") | record, category `mood` | `POST /records` |
| a diary entry or something that happened | record, category `journal` | `POST /records` |
| took a medicine or supplement | record, category `medication` | `POST /records` |
| a symptom ("头疼") | record, category `symptom` | `POST /records` |
| spent money | record, category `expense` | `POST /records` |
| smoked ("抽了根烟", "下午抽了3根") | record, category `smoking` | `POST /records` |
| how they slept, subjectively | record, category `sleep` | `POST /records` |
| a single number: weight, water, blood pressure, temperature | **sample** | `POST /samples` |
| a stable fact: height, birthday, goals, allergies | **profile** key | `PATCH /profile` |
| something that fits none of the above (reading, study hours, skincare…) | create a **category**, then record | `POST /categories` |

- **Records** are structured events. Each has a time (`start`, optional `end`), a `category`,
  `data` that matches the category's JSON Schema, plus `title`, `note` and `tags`.
- **Samples** are numbers in a time series: `{metric, value, unit, start}`. Apple Health
  imports write millions of them, including steps, heart rate and sleep stages.
- **Foods** are the personal food library: nutrition for things the user eats repeatedly.
- **Profile** holds key/value facts about the person.

`GET /api/v1/categories/{name}` shows a category's schema and a complete example. A category's
`data` accepts extra fields beyond its schema, so keep useful detail rather than dropping it.

## 5. Time

- Times are ISO 8601. Always include the offset: `2026-11-02T12:30:00+09:00`.
- A time without an offset is read in the profile timezone. A bare date `2026-11-02` means
  12:00 on that day, a bare clock time `12:30` means today, and an omitted `start` means now.
- Convert relative phrases yourself from `now` in `/context`. Use these defaults when the user
  gives no clock time:

  | Phrase | Time |
  |---|---|
  | 早上 / 早饭 | 08:00 |
  | 中午 / 午饭 | 12:30 |
  | 下午 | 15:30 |
  | 晚上 / 晚饭 | 19:00 |
  | 夜宵 | 22:30 |
  | 昨天… | the same time, one day earlier |

- `local_date` is the calendar day in the record's own offset. Daily views group by it, so a
  meal logged with a `+08:00` time during a trip to China counts on the local day there.

## 6. Recipes

### 6.1 Log a meal (the most common task)

User: "我中午吃了一碗牛肉面，加了个煎蛋"

1. If you have not loaded context this session, call `GET /api/v1/context`. Then check the
   food library with `GET /api/v1/foods?q=牛肉面`.
2. Estimate each item for the amount actually eaten: `grams`, `kcal`, `protein_g`, `fat_g`,
   `carbs_g`, plus `fiber_g`, `sugar_g` and `sodium_mg` when you can estimate them. Set
   `estimated: true` and `confidence`. Use typical portions for the cuisine and the place.
   A noodle-shop bowl is bigger than a home bowl, and Japanese convenience-store items have
   labels you probably know.
3. Write the meal:

```http
POST {BASE}/api/v1/records
Authorization: Bearer <token>
Content-Type: application/json

{
  "category": "meal",
  "start": "2026-11-02T12:30:00+09:00",
  "data": {
    "meal_type": "lunch",
    "items": [
      {"name": "牛肉面", "amount": 1, "unit": "碗", "grams": 550, "kcal": 620,
       "protein_g": 30, "fat_g": 16, "carbs_g": 88, "sodium_mg": 2400,
       "estimated": true, "confidence": "medium"},
      {"name": "煎蛋", "amount": 1, "unit": "个", "grams": 50, "kcal": 90,
       "protein_g": 6.3, "fat_g": 7, "carbs_g": 0.4, "estimated": true, "confidence": "high"}
    ],
    "estimated": true,
    "estimation_note": "standard noodle-shop bowl; egg fried in ~3 g oil"
  },
  "note": "我中午吃了一碗牛肉面，加了个煎蛋"
}
```

The response is `{"record": {...}, "warnings": [...], "created": true}`.

- The server sums the item nutrients into `record.data.totals`.
- If you omit `meal_type`, the server fills it from the time.
- The server generates `title` (here `午餐：牛肉面、煎蛋`).
- `warnings` flags problems such as an item with no `kcal`. Fix them with a PATCH.

4. **Reuse foods.** If a library food matches, reference it and let the server compute the
   nutrients: `{"name": "牛肉面", "food_id": "food_...", "amount": 1, "unit": "碗"}`, or give
   `grams` to scale `per_100g`. You may still override any nutrient explicitly.
5. **Grow the library** when the user gives exact numbers (from a package label) or names a
   food they clearly eat often: `POST /api/v1/foods`.

```json
{"name": "セブン 鮭おにぎり", "aliases": ["鮭饭团"], "brand": "7-Eleven",
 "serving": {"amount": 1, "unit": "个", "grams": 110},
 "per_serving": {"kcal": 180, "protein_g": 4.9, "fat_g": 1.6, "carbs_g": 37.4,
                 "sodium_mg": 390},
 "source": "label"}
```

Nutrient keys, used everywhere: `kcal`, `protein_g`, `fat_g`, `carbs_g`, `fiber_g`, `sugar_g`,
`saturated_fat_g`, `sodium_mg` (salt in grams × 393), `cholesterol_mg`, `caffeine_mg`,
`alcohol_g`, `water_ml`.

### 6.2 Correct a meal

User: "刚才那个面其实只吃了半碗"

```http
PATCH {BASE}/api/v1/records/rec_...
{"data": {"items": [ ...the full corrected items array... ]}}
```

`data` is a JSON merge patch: objects merge, `null` deletes a key, and **arrays replace**, so
always send the complete `items` list. The server recomputes the totals. To remove a record,
use `DELETE /records/{id}`; `POST /records/{id}/restore` undoes it.

### 6.3 Weight, water and other measurements

```http
POST {BASE}/api/v1/samples
{"samples": [
  {"metric": "body_mass", "value": 61.8, "unit": "kg", "start": "2026-11-02T07:30:00+09:00"},
  {"metric": "dietary_water", "value": 500, "unit": "mL"}
]}
```

Common metric names:

| Group | Metrics |
|---|---|
| Body | `body_mass` (kg), `body_fat_percentage` (%), `waist_circumference` (cm) |
| Vitals | `blood_pressure_systolic` / `blood_pressure_diastolic` (mmHg), `blood_glucose` (mg/dL), `body_temperature` (degC), `heart_rate` (count/min) |
| Intake and activity | `dietary_water` (mL), `step_count`, `mindful_minutes` (min) |

`GET /api/v1/metrics` lists them all. An unknown metric name is created automatically. To set
how it aggregates by day (`sum`, `avg` or `last`), declare it first with `POST /metrics`.

### 6.4 A workout the user tells you about

```json
{"category": "workout", "start": "2026-11-02T07:00:00+09:00",
 "end": "2026-11-02T07:35:00+09:00",
 "data": {"activity_type": "running", "distance_km": 5.2, "active_kcal": 330, "rpe": 6}}
```

Apple Watch workouts are imported automatically, so before adding one, check that the same
workout is not already there: `GET /records?category=workout&date=YYYY-MM-DD`. If it is, PATCH
it with what the user added (for example `rpe`, or a `note`).

### 6.5 Mood, diary, symptoms, medication, expenses

```json
{"category": "mood", "data": {"score": 4, "emotions": ["焦虑"], "stress": 8,
 "reasons": ["作业截止"]}, "note": "原话…"}
{"category": "journal", "title": "第一次去代代木公园", "data": {"text": "…"}}
{"category": "symptom", "data": {"name": "头痛", "severity": 5, "body_part": "太阳穴"}}
{"category": "medication", "data": {"name": "布洛芬", "dose": 200, "unit": "mg",
 "kind": "medicine", "reason": "头痛"}}
{"category": "expense", "data": {"amount": 980, "currency": "JPY", "merchant": "松屋",
 "kind": "food"}}
```

### 6.6 Cigarettes

The user tracks how much they smoke. Each `smoking` record is one occasion holding
`data.count` cigarettes. The user also taps a +1 button on their web page each time they light
one, which writes `{"count": 1}` with source `manual`, so some cigarettes are already there
before they talk to you.

| The user says… | Do |
|---|---|
| "抽了一根" / "刚抽了根烟" | `POST /records` with `{"category": "smoking", "data": {"count": 1}}`; `start` defaults to now |
| "下午抽了3根" | one record with `count: 3` and `start` in the afternoon, never later than `now` (15:30, or now if it is earlier than that) |
| "今天一共抽了10根" | read `smoking.cigarettes` from `GET /summary/day` first, then write one record with only the difference. If it is already 10 or more, write nothing and say what is recorded |
| "多记了一根" / "刚才那根不算" | take back the latest entry: PATCH its `count` down by one, or DELETE it when `count` is 1 |

```json
{"category": "smoking", "data": {"count": 1, "trigger": "饭后"}, "note": "吃完饭抽了一根"}
```

- Optional fields: `kind` (`heated` for IQOS or glo, `e_cigarette`, `cigar`; omit it for
  ordinary cigarettes), `brand`, and `trigger`, what led to it in the user's own words.
- `GET /summary/day` has a `smoking` object: `cigarettes` (the day's total), `daily_max` (the
  user's own cap, from profile `goals.cigarettes_max`, or null; "每天最多8根" is
  `PATCH /profile {"goals": {"cigarettes_max": 8}}`), `yesterday`, `avg_prev_7d`,
  `last_at` (the latest cigarette up to that day), `tracking_since` and `entries`. It is `null`
  until the first cigarette is logged. From then on, a day with no entries counts as 0, a
  smoke-free day. Rows of `GET /summary/days` carry `cigarettes` the same way.
- Record and report without judgment, and don't lecture. Confirm with the day's total, and
  the cap if they set one: "记好了：今天第 6 根（上限 8 根）".

### 6.7 A new kind of record

When nothing fits, first check `GET /categories` for one that does. If none fits, create one:

```http
POST {BASE}/api/v1/categories
{"name": "reading", "title": "读书", "description": "Books read: title, pages, minutes.",
 "schema": {"type": "object", "required": ["book"],
            "properties": {"book": {"type": "string"}, "pages": {"type": "integer"},
                           "minutes": {"type": "number"}}},
 "example": {"data": {"book": "百年孤独", "pages": 40, "minutes": 50}}}
```

Write the `description` for the next agent that will use it. Names are snake_case.

### 6.8 Answer questions

| Question | Call |
|---|---|
| 今天吃了多少 | `GET /summary/day` (default today), then `nutrition.intake` |
| 还能吃多少 / 吃多了要走多久 | `GET /summary/day`, then `energy` (below) |
| 这周 / 这个月怎么样 | `GET /summary/days?from=2026-10-27&to=2026-11-02` |
| 今天抽了几根 / 这周抽了多少 | `GET /summary/day`, then `smoking`; for a range, `cigarettes` in each row of `GET /summary/days` |
| a trend in one metric | `GET /metrics/step_count/series?from=…&to=…&bucket=day\|week\|month\|hour` |
| 上次吃拉面是什么时候 | `GET /records?category=meal&q=拉面&limit=5` |
| everything on one day | `GET /records?date=2026-11-01&order=asc` |

Lists return `next_cursor` when there is more. Pass it back as `cursor` to get the next page.

**The energy bar.** The owner is losing weight and tracks it as one bar: everything eaten pushes
it up, and resting and active burn both pull it down. `energy` in `/summary/day` holds it:

- `balance_kcal`: eaten − resting − active so far, plus `carry_kcal`. A meal timed later today
  counts tonight, not yet now. `carry_kcal` is yesterday's own eaten − burn against its own
  line, without what carried into yesterday, so nothing older carries over.
- `balance_tonight_kcal`: where the bar ends at midnight if nothing more is eaten or done.
  The resting burn still to come is added up to `basal_expected_kcal` (the 7-day average, or
  the 30-day one when the week has none); activity is not guessed. On past days it is the
  final value. It is null when there is no resting-burn history to estimate from.
- `target_kcal`: the line, `goals.kcal` minus `average_burn_kcal` (the average daily burn over
  the 30 days before). Eating the goal on an ordinary day lands on it. It is null when
  `goals.kcal` is not set or none of those 30 days has burn data.
- `over_target_kcal`: tonight minus the line. Positive means over by that much; negative means
  that much room is left to eat. Today, `walk_minutes` and `walk_steps` are the walking that
  would bring tonight back to the line, at the owner's own pace from their recent walks
  (`walk_kcal_per_min`, `walk_steps_per_min`). A past day's `over_target_kcal` already
  includes its `carry_kcal`; to total several days, add up `over_target_kcal − carry_kcal`.
- `logged: false` means no meal was recorded that day, so the balance is unknown. Say that
  instead of reporting a big deficit.

Answer "还能吃多少" or "要走多久" from these numbers, for example "今晚预计 +50，比目标线高
857，要抵掉约走 3 小时 24 分（约 19,500 步）". Don't lecture.

### 6.9 Photos and files

To attach a photo to a record, send the raw file as the body:

```http
POST {BASE}/api/v1/records/{id}/attachments?filename=lunch.jpg
Content-Type: image/jpeg

<bytes>
```

A multipart form with the field `file` also works. `GET /attachments/{id}` downloads a file.

### 6.10 Profile and goals

```http
PATCH {BASE}/api/v1/profile
{"height_cm": 172, "goals": {"kcal": 2000, "protein_g": 110, "steps": 8000},
 "diet": {"allergies": ["花生"], "preferences": ["少油"]}}
```

`null` deletes a key. `goals` merges one level down: send only the goals you change, and a goal
set to `null` is removed; the others stay. `goals.kcal` also sets the energy bar's line (§6.8).
`goals.cigarettes_max` is a daily cap on cigarettes, not a target to reach. The `timezone` key drives every day boundary. Change it when the user
moves, not for short trips: times with an explicit offset already land on the right local day.

## 7. Apple Health and Apple Fitness

Two ways in. Both write the same metrics and the same workout records, and repeats are
deduplicated.

- **Full history:** In the Health app, tap your profile picture, then Export All Health Data.
  Upload the zip with `POST /imports/apple-health` (raw body or multipart `file`). The upload
  returns `import_id` at once and runs in the background; poll `GET /imports/{id}`. From a
  shell on the mini PC, `lifeos import apple-health export.zip` does the same.
- **Ongoing sync:** Use the Health Auto Export app, REST API automation, JSON format, workouts
  Version 2. Point it at `POST /imports/health-auto-export` with an
  `Authorization: Bearer <token>` header. Use hourly aggregation, or turn Summarize off. Daily
  totals double-count if both a day-level summary and raw samples cover the same day.

What the imports produce:

- Steps, energy, heart rate, HRV, SpO₂, weight and every other quantity become **samples**.
- Sleep stages become the samples `sleep_core`, `sleep_deep`, `sleep_rem`, `sleep_awake` and
  `sleep_in_bed`. They are dated by the morning you woke up.
- Workouts become `workout` **records** with source `apple_health`. Routes are attached as GPX
  files.
- With Health Auto Export's "Include Workout Metrics" option on, each workout also carries
  **series**. These include heart rate during the workout and for the 2 minutes after it,
  energy and distance per minute, steps, and cycling cadence, power and speed. They stay
  attached to the workout and never count toward daily totals.
  - Read them with `GET /records/{id}/series`. Add `?name=heart_rate` for one series, or
    `?points=false` for statistics only.
  - The headline numbers are already in the record's `data`: `avg_hr`, `max_hr`,
    `hr_recovery_1min` and `hr_recovery_2min` (the drop in bpm after the workout ends),
    `avg_speed_kmh` and `effort_score`.
  - Workouts imported from the export file get their heart-rate curve from the watch's
    heart-rate samples inside the workout window.
- Activity rings become one `activity_summary` record per day.

When iPhone and Watch both record the same thing, daily totals count each hour once, taking
the larger source. Do not add the sources yourself; use `/summary/*` and `/metrics/*/series`.
The original upload files are kept under the data directory.

## 8. Reference

All paths are under `{BASE}/api/v1`. **R** = read scope, **W** = write, **A** = admin.

| Method and path | Scope | Purpose |
|---|---|---|
| GET `/context` | R | session bootstrap (see §3) |
| GET `/whoami` | R | your token's name and scope |
| GET `/categories` · GET `/categories/{name}` | R | categories, with schema and example |
| POST `/categories` · PATCH `/categories/{name}` | W | create or edit a custom category |
| DELETE `/categories/{name}` | A | delete an empty custom category |
| GET `/records` | R | filters: `category` (comma list), `date`, `from`, `to`, `q`, `tag`, `source`, `include_deleted`, `order`, `limit`, `cursor` |
| POST `/records` · POST `/records/batch` | W | create; with `external_id`, update in place instead |
| GET · PATCH · DELETE `/records/{id}` | R/W | read, merge-patch, soft-delete |
| POST `/records/{id}/restore` | W | undo a delete |
| GET `/records/{id}/series` | R | time series of a record: workout heart rate, recovery, cadence, power; `name`, `points=false` |
| POST `/records/{id}/attachments` · GET `/attachments/{id}` | W/R | files |
| GET `/metrics` · POST `/metrics` | R/W | metric catalog, declare a metric |
| GET `/metrics/{name}/series` | R | `from`, `to`, `bucket=hour\|day\|week\|month` |
| GET `/samples` · POST `/samples` · DELETE `/samples/{id}` | R/W | raw numbers |
| GET `/foods?q=` · POST `/foods` · GET/PATCH/DELETE `/foods/{id}` | R/W | food library |
| GET `/profile` · PATCH `/profile` | R/W | facts and goals |
| GET `/summary/day?date=` · GET `/summary/days?from=&to=` | R | daily views |
| GET `/changes` | R | audit log: who changed what, with before and after |
| POST `/imports/apple-health` · POST `/imports/health-auto-export` | W | Apple Health in |
| GET `/imports` · GET `/imports/{id}` | R | import status |
| GET `/export` · GET `/export/samples.ndjson` | R | everything out |
| GET/POST `/tokens` · DELETE `/tokens/{name}` | A | agent tokens |

**Errors** look like `{"error": {"code", "message", "hint"}}`:

| Status | Meaning |
|---|---|
| 400 | bad input |
| 401 | missing or invalid token |
| 403 | the token's scope is too low |
| 404 | not found; the hint lists what exists |
| 409 | conflict |
| 422 | the data does not match the category schema; the message names the field |

Read the `hint`: it usually says exactly what to do next.

**Idempotency:** when you sync from another system, set `source` (for example `"notion"`) and a
stable `external_id`. Resending the same pair updates the existing record instead of adding a
duplicate.
