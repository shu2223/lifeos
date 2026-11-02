"""Record categories: what kinds of structured entries exist and what their `data` looks like.

Built-in categories are seeded on startup. Agents may add their own with a JSON Schema.
Some categories have server-side derivation (meal totals, workout duration) in `derive`.
"""
import json

NUTRIENTS = {
    "kcal": "energy (kcal)",
    "protein_g": "protein (g)",
    "fat_g": "fat (g)",
    "carbs_g": "carbohydrate (g)",
    "fiber_g": "dietary fibre (g)",
    "sugar_g": "sugars (g)",
    "saturated_fat_g": "saturated fat (g)",
    "sodium_mg": "sodium (mg); salt g × 393 = sodium mg",
    "cholesterol_mg": "cholesterol (mg)",
    "caffeine_mg": "caffeine (mg)",
    "alcohol_g": "alcohol (g)",
    "water_ml": "water from drinks (ml)",
}
NUTRIENT_PROPS = {k: {"type": "number", "minimum": 0, "description": v}
                  for k, v in NUTRIENTS.items()}
MEAL_TYPES = {"breakfast": "早餐", "lunch": "午餐", "dinner": "晚餐", "snack": "加餐",
              "late_night": "夜宵", "drink": "饮品", "other": "其他"}
LEVEL_1_10 = {"type": "integer", "minimum": 1, "maximum": 10}

_MEAL_ITEM = {
    "type": "object",
    "required": ["name"],
    "properties": {
        "name": {"type": "string", "minLength": 1, "description": "what was eaten, as the user said it"},
        "amount": {"type": "number", "exclusiveMinimum": 0, "description": "how many `unit`s"},
        "unit": {"type": "string", "description": "碗, 个, 杯, 片, g, ml, serving ..."},
        "grams": {"type": "number", "minimum": 0, "description": "estimated edible weight"},
        "food_id": {"type": "string", "description": "id from /foods; missing nutrients are filled from it"},
        "estimated": {"type": "boolean", "description": "true when the numbers are the agent's estimate"},
        "confidence": {"enum": ["low", "medium", "high"]},
        **NUTRIENT_PROPS,
    },
}

BUILTIN: dict[str, dict] = {
    "meal": {
        "title": "饮食",
        "description": (
            "Anything eaten or drunk. One record per meal or snack, one item per food. "
            "Give per-item nutrients for the amount actually eaten; the server sums them into "
            "data.totals and fills meal_type from the time if omitted."
        ),
        "schema": {
            "type": "object",
            "required": ["items"],
            "properties": {
                "meal_type": {"enum": list(MEAL_TYPES)},
                "items": {"type": "array", "minItems": 1, "items": _MEAL_ITEM},
                "place": {"type": "string", "description": "home, canteen, restaurant name ..."},
                "estimated": {"type": "boolean"},
                "estimation_note": {"type": "string", "description": "assumptions behind the numbers"},
                "totals": {"type": "object", "description": "computed by the server; input ignored"},
            },
        },
        "example": {
            "category": "meal",
            "start": "2026-11-02T12:30:00+09:00",
            "data": {
                "meal_type": "lunch",
                "items": [
                    {"name": "牛肉面", "amount": 1, "unit": "碗", "grams": 550, "kcal": 620,
                     "protein_g": 30, "fat_g": 16, "carbs_g": 88, "sodium_mg": 2400,
                     "estimated": True, "confidence": "medium"},
                    {"name": "煎蛋", "amount": 1, "unit": "个", "grams": 50, "kcal": 90,
                     "protein_g": 6.3, "fat_g": 7, "carbs_g": 0.4, "estimated": True,
                     "confidence": "high"},
                ],
                "estimated": True,
                "estimation_note": "standard noodle-shop bowl; egg fried in ~3 g oil",
            },
        },
    },
    "workout": {
        "title": "运动",
        "description": (
            "A workout or training session. Apple Watch workouts arrive here automatically "
            "(source apple_health). For workouts the user tells you about, set start and end; "
            "duration_min is computed from them when omitted."
        ),
        "schema": {
            "type": "object",
            "required": ["activity_type"],
            "properties": {
                "activity_type": {"type": "string",
                                  "description": "snake_case: running, walking, cycling, "
                                                 "strength_training, yoga, swimming, hiking, "
                                                 "hiit, dance, other ..."},
                "activity_name": {"type": "string", "description": "display name as recorded"},
                "duration_min": {"type": "number", "minimum": 0},
                "active_kcal": {"type": "number", "minimum": 0},
                "total_kcal": {"type": "number", "minimum": 0},
                "distance_km": {"type": "number", "minimum": 0},
                "avg_hr": {"type": "number"}, "max_hr": {"type": "number"},
                "min_hr": {"type": "number"},
                "elevation_up_m": {"type": "number"},
                "hr_recovery_1min": {"type": "number",
                                     "description": "heart-rate drop (bpm) 1 min after the end"},
                "hr_recovery_2min": {"type": "number",
                                     "description": "heart-rate drop (bpm) 2 min after the end"},
                "avg_speed_kmh": {"type": "number"},
                "effort_score": {"type": "number", "description": "Apple workout effort, 1-10"},
                "step_count": {"type": "number"},
                "indoor": {"type": "boolean"},
                "rpe": {**LEVEL_1_10, "description": "perceived exertion"},
                "sets": {"type": "array", "items": {
                    "type": "object", "required": ["exercise"],
                    "properties": {"exercise": {"type": "string"}, "sets": {"type": "integer"},
                                   "reps": {"type": "integer"}, "weight_kg": {"type": "number"}},
                }},
            },
        },
        "example": {
            "category": "workout",
            "start": "2026-11-02T07:00:00+09:00",
            "end": "2026-11-02T07:35:00+09:00",
            "data": {"activity_type": "running", "distance_km": 5.2, "active_kcal": 330, "rpe": 6},
        },
    },
    "activity_summary": {
        "title": "活动圆环",
        "description": "Apple Fitness daily rings (move, exercise, stand). One record per day, "
                       "written by the Apple Health import.",
        "schema": {
            "type": "object",
            "properties": {k: {"type": "number"} for k in (
                "move_kcal", "move_goal_kcal", "exercise_min", "exercise_goal_min",
                "stand_hours", "stand_goal_hours", "move_time_min", "move_time_goal_min")},
        },
        "example": {
            "category": "activity_summary",
            "start": "2026-11-02",
            "data": {"move_kcal": 412, "move_goal_kcal": 500, "exercise_min": 34,
                     "exercise_goal_min": 30, "stand_hours": 11, "stand_goal_hours": 12},
        },
    },
    "sleep": {
        "title": "睡眠",
        "description": (
            "A night's sleep as the user describes it (how it felt, bedtime). Apple Watch sleep "
            "stages are samples (sleep_* metrics), not records; use this for what the watch "
            "cannot know. start = went to bed, end = got up."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "quality": {"type": "integer", "minimum": 1, "maximum": 5},
                "asleep_min": {"type": "number"}, "in_bed_min": {"type": "number"},
                "awakenings": {"type": "integer"}, "dreams": {"type": "string"},
            },
        },
        "example": {"category": "sleep", "start": "2026-11-01T23:40:00+09:00",
                    "end": "2026-11-02T07:10:00+09:00",
                    "data": {"quality": 2, "awakenings": 3}, "note": "睡前喝了咖啡"},
    },
    "mood": {
        "title": "心情",
        "description": "How the user feels. score 1 (awful) to 10 (great).",
        "schema": {
            "type": "object",
            "required": ["score"],
            "properties": {
                "score": LEVEL_1_10,
                "emotions": {"type": "array", "items": {"type": "string"}},
                "energy": LEVEL_1_10, "stress": LEVEL_1_10,
                "reasons": {"type": "array", "items": {"type": "string"}},
            },
        },
        "example": {"category": "mood",
                    "data": {"score": 7, "emotions": ["平静", "有点累"], "energy": 5}},
    },
    "journal": {
        "title": "日记",
        "description": "Free-form diary entries and notes about the user's day or life.",
        "schema": {"type": "object", "required": ["text"],
                   "properties": {"text": {"type": "string", "minLength": 1}}},
        "example": {"category": "journal", "title": "第一次去代代木公园",
                    "data": {"text": "下午和朋友去了代代木公园……"}},
    },
    "medication": {
        "title": "用药与补剂",
        "description": "A medicine or supplement taken. One record per dose.",
        "schema": {
            "type": "object",
            "required": ["name"],
            "properties": {
                "name": {"type": "string"}, "dose": {"type": "number"},
                "unit": {"type": "string", "description": "mg, 片, 粒, ml ..."},
                "kind": {"enum": ["medicine", "supplement"]},
                "reason": {"type": "string"},
            },
        },
        "example": {"category": "medication",
                    "data": {"name": "维生素D", "dose": 1000, "unit": "IU", "kind": "supplement"}},
    },
    "symptom": {
        "title": "身体状况",
        "description": "A symptom or physical complaint. severity 1 (barely) to 10 (worst).",
        "schema": {
            "type": "object",
            "required": ["name"],
            "properties": {"name": {"type": "string"}, "severity": LEVEL_1_10,
                           "body_part": {"type": "string"}},
        },
        "example": {"category": "symptom", "data": {"name": "头痛", "severity": 4}},
    },
    "expense": {
        "title": "消费",
        "description": "Money spent. amount in `currency` (ISO 4217, e.g. JPY, CNY).",
        "schema": {
            "type": "object",
            "required": ["amount", "currency"],
            "properties": {
                "amount": {"type": "number"},
                "currency": {"type": "string", "pattern": "^[A-Z]{3}$"},
                "merchant": {"type": "string"},
                "kind": {"type": "string", "description": "food, transport, shopping, rent ..."},
                "payment_method": {"type": "string"},
                "meal_record_id": {"type": "string", "description": "link to a meal record"},
            },
        },
        "example": {"category": "expense",
                    "data": {"amount": 980, "currency": "JPY", "merchant": "松屋",
                             "kind": "food"}},
    },
}


def seed(conn, now: str) -> None:
    for name, c in BUILTIN.items():
        conn.execute(
            "INSERT INTO categories(name, title, description, schema, example, builtin,"
            " created_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 1, 'system', ?, ?)"
            " ON CONFLICT(name) DO UPDATE SET description = excluded.description,"
            " schema = excluded.schema, example = excluded.example, builtin = 1",
            (name, c["title"], c["description"], json.dumps(c["schema"], ensure_ascii=False),
             json.dumps(c["example"], ensure_ascii=False), now, now),
        )


def _round(x: float) -> float:
    return round(x, 1)


def _scaled(nutrients: dict, factor: float) -> dict:
    return {k: _round(v * factor) for k, v in nutrients.items()
            if k in NUTRIENTS and isinstance(v, (int, float))}


def fill_from_food(item: dict, food: dict, warnings: list[str], label: str) -> None:
    """Fill an item's missing grams and nutrients from a /foods entry."""
    serving = food.get("serving") or {}
    s_amount = serving.get("amount") or 1
    same_unit = item.get("unit") in (None, serving.get("unit"))
    if item.get("grams") is None and serving.get("grams") and same_unit:
        item["grams"] = _round((item.get("amount") or s_amount) / s_amount * serving["grams"])
        item.setdefault("unit", serving.get("unit"))
    if food.get("per_100g") and item.get("grams") is not None:
        filled = _scaled(food["per_100g"], item["grams"] / 100)
    elif food.get("per_serving") and same_unit:
        filled = _scaled(food["per_serving"], (item.get("amount") or s_amount) / s_amount)
    else:
        warnings.append(f"{label}: could not scale food {food['id']} (give grams, or use "
                        f"its serving unit {serving.get('unit')!r})")
        return
    for k, v in filled.items():
        item.setdefault(k, v)
    item.setdefault("estimated", food.get("source") == "estimate")


def _meal_type_for(hour: int, minute: int) -> str:
    t = hour * 60 + minute
    if 300 <= t < 630:
        return "breakfast"
    if 630 <= t < 900:
        return "lunch"
    if 900 <= t < 1020:
        return "snack"
    if 1020 <= t < 1290:
        return "dinner"
    return "late_night"


def derive(conn, category: str, data: dict, start, end, warnings: list[str]) -> dict:
    """Server-side fields. `start`/`end` are datetimes (end may be None)."""
    if category == "meal":
        from .foods import get_food, touch_food
        if "meal_type" not in data:
            data["meal_type"] = _meal_type_for(start.hour, start.minute)
        totals: dict[str, float] = {}
        for i, item in enumerate(data.get("items", [])):
            label = f"items[{i}] {item.get('name', '')}"
            if item.get("food_id"):
                food = get_food(conn, item["food_id"])
                if food is None:
                    warnings.append(f"{label}: food_id {item['food_id']} not found")
                else:
                    fill_from_food(item, food, warnings, label)
                    touch_food(conn, food["id"])
            if "kcal" not in item:
                warnings.append(f"{label}: no kcal; daily totals will undercount")
            for k in NUTRIENTS:
                v = item.get(k)
                if isinstance(v, (int, float)):
                    totals[k] = totals.get(k, 0) + v
        data["totals"] = {k: _round(v) for k, v in totals.items()}
    elif category == "workout":
        if "duration_min" not in data and end is not None:
            data["duration_min"] = _round((end - start).total_seconds() / 60)
    return data


def default_title(category: str, cat_title: str, data: dict) -> str:
    if category == "meal":
        names = "、".join(i.get("name", "") for i in data.get("items", [])[:6])
        more = "等" if len(data.get("items", [])) > 6 else ""
        return f"{MEAL_TYPES.get(data.get('meal_type'), '饮食')}：{names}{more}"
    if category == "workout":
        name = data.get("activity_name") or data.get("activity_type", "")
        bits = [name]
        if data.get("distance_km"):
            bits.append(f"{data['distance_km']:g} km")
        if data.get("duration_min"):
            bits.append(f"{data['duration_min']:.0f} 分钟")
        return f"运动：{' · '.join(b for b in bits if b)}"
    if category == "mood" and "score" in data:
        return f"心情 {data['score']}/10"
    for key in ("name", "text", "merchant"):
        if isinstance(data.get(key), str) and data[key]:
            return f"{cat_title}：{data[key][:40]}"
    return cat_title
