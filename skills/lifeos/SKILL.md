---
name: lifeos
description: Record and look up the user's personal life data in lifeos, their self-hosted records service — meals and calories, drinks and water, workouts, weight and body measurements, sleep, mood, diary, symptoms, medication, spending, Apple Health and Apple Fitness data. Use whenever the user mentions eating or drinking something ("我今天吃了…", "喝了杯咖啡"), exercising, weighing themselves, how they slept or feel, or asks about their own intake, activity, health trends or habits.
---

# lifeos: the user's personal records

lifeos is the user's own record store, running on their home server. When they tell you about
their day, write it there. When they ask about themselves, read it from there instead of
guessing.

## Connecting

- **API root**: `$LIFEOS_URL`, for example `https://yishuapp.com/life/api/v1`. On the server
  itself it is `http://127.0.0.1:8773/api/v1`. Every call goes under this address. Other paths
  on the same site may sit behind the owner's login.
- **Token**: `$LIFEOS_TOKEN`. Every agent has its own token, which the owner creates with
  `lifeos token create <agent-name>`.
- If either is missing, ask the user once.
- Never print the token, repeat it, or write it into a record.

Every call sends `Authorization: Bearer $LIFEOS_TOKEN`. Request bodies are JSON. Read the full
manual once per session before your first write: `GET $LIFEOS_URL/manual`. It covers every
endpoint, schema and recipe.

```bash
H="Authorization: Bearer $LIFEOS_TOKEN"
curl -s "$LIFEOS_URL/context" -H "$H"
curl -s -X POST "$LIFEOS_URL/records" -H "$H" -H "Content-Type: application/json" \
  --data-binary @- <<'JSON'
{"category": "meal", "start": "12:30", "data": {"items": [
  {"name": "牛肉面", "amount": 1, "unit": "碗", "grams": 550, "kcal": 620, "protein_g": 30,
   "fat_g": 16, "carbs_g": 88, "estimated": true, "confidence": "medium"}]},
 "note": "我中午吃了一碗牛肉面"}
JSON
```

On the server itself, `lifeos api GET context` and `lifeos api POST records <<'JSON' …` do the
same thing using the server's own token.

Paths in the table are relative to `$LIFEOS_URL`.

| Call | Use |
|---|---|
| `GET /context` | start here: current time, timezone, profile and goals, today so far, frequent foods |
| `GET /foods?q=牛肉面` | the user's food library |
| `POST /records` | a meal, workout, mood, diary entry, symptom, medication or expense |
| `PATCH /records/{id}` | fix a record; `data` is merged and arrays are replaced |
| `DELETE /records/{id}` | remove a record (it can be restored) |
| `POST /samples` | numbers such as weight or water: `{"samples": [{"metric": "body_mass", "value": 61.8}]}` |
| `GET /summary/day` | today's intake against goals, plus steps, sleep and workouts |
| `GET /records/{id}/series` | a workout's heart-rate curve, recovery, cadence, power (`?points=false` for stats only) |

## How to behave

- **One message should be enough.** "我中午吃了一碗牛肉面加个煎蛋" becomes a complete `meal`
  record in a single turn. Estimate grams, kcal, protein, fat and carbs for each item, and
  mark them `estimated` with a `confidence`. Ask a question only if any guess would be wildly
  off.
- Take the date and time from `GET /context`, not from your own sense of the date.
  Times without an offset are read in the user's timezone.
- Check `frequent_foods` and `GET /foods?q=` first, so foods the user eats often get
  consistent numbers. When the user gives numbers from a label, save the food with
  `POST /foods`.
- Fix mistakes with `PATCH`. Remove duplicates with `DELETE`. Never write a second record to
  fix the first.
- Workouts, steps, sleep and heart rate from Apple Watch arrive automatically, so don't enter
  them again. Add what the user tells you to the existing record instead.
- After writing, confirm in one short line in the user's language, with the key numbers and
  today's running total. For example: 记好了：午餐 牛肉面+煎蛋 ≈ 710 kcal，今天累计 1,240 / 2,000 kcal.
