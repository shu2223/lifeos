"""Record categories: what kinds of structured entries exist and what their `data` looks like.

Built-in categories are seeded on startup. Agents may add their own with a JSON Schema.
Some categories have server-side derivation (meal totals, workout duration) in `derive`.
"""
import json

from .errors import ApiError

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
# Names agents write instead of the keys above, matched without case. They are renamed on write,
# so an item's "calories": 650 counts toward the totals instead of being stored and ignored.
NUTRIENT_ALIASES = {
    "calories": "kcal", "calorie": "kcal", "cal": "kcal", "kcals": "kcal", "energy_kcal": "kcal",
    "protein": "protein_g", "fat": "fat_g", "carbs": "carbs_g", "carb": "carbs_g",
    "carbohydrate": "carbs_g", "carbohydrates": "carbs_g", "carbohydrate_g": "carbs_g",
    "carbohydrates_g": "carbs_g", "fiber": "fiber_g", "fibre": "fiber_g", "fibre_g": "fiber_g",
    "sugar": "sugar_g", "sugars": "sugar_g", "sugars_g": "sugar_g",
}
MEAL_TYPES = {"breakfast": "早餐", "lunch": "午餐", "dinner": "晚餐", "snack": "加餐",
              "late_night": "夜宵", "drink": "饮品", "other": "其他"}
LEVEL_1_10 = {"type": "integer", "minimum": 1, "maximum": 10}
SMOKING_KINDS = {"cigarette": "香烟", "heated": "加热烟", "e_cigarette": "电子烟", "cigar": "雪茄",
                 "other": "其他"}

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
    "smoking": {
        "title": "抽烟",
        "description": (
            "Cigarettes smoked. One record per occasion: count 1 for each cigarette as it "
            "happens (the web page's +1 button writes these), or the number the user gives for "
            "a stretch of time. The day's total is the sum of count, shown in /summary/day under "
            "smoking; read it before writing a total for the day, so cigarettes the user already "
            "tapped in are not counted twice."
        ),
        "schema": {
            "type": "object",
            "required": ["count"],
            "properties": {
                "count": {"type": "integer", "minimum": 1, "maximum": 100,
                          "description": "cigarettes in this record"},
                "kind": {"enum": list(SMOKING_KINDS),
                         "description": "omit for ordinary cigarettes; heated = IQOS, glo ..."},
                "brand": {"type": "string"},
                "trigger": {"type": "string",
                            "description": "what led to it, in the user's words: 饭后, 压力大, 喝酒 ..."},
            },
        },
        "example": {"category": "smoking", "start": "2026-11-02T13:10:00+09:00",
                    "data": {"count": 1, "trigger": "饭后"}},
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


def _nutrient_key(key: str, value) -> str | None:
    """The NUTRIENTS key that `key` stands for, if `value` was meant as an amount. "650" and
    "650 kcal" were, and then fail validation as kcal does; "sugar": false on a coffee was not."""
    if value is None or isinstance(value, bool) or (
            isinstance(value, str) and not any(c.isdigit() for c in value)):
        return None
    low = key.lower()
    return low if low in NUTRIENTS else NUTRIENT_ALIASES.get(low)


def canonical_nutrients(d: dict) -> list[tuple[str, str, bool]]:
    """Rename nutrient aliases in `d` to their NUTRIENTS key. Returns (alias, key, used) for each;
    when `d` already has the key, its value wins and the alias is dropped unused."""
    renamed = []
    for key in list(d):
        to = _nutrient_key(key, d[key])
        if to is None or to == key:
            continue
        value = d.pop(key)
        renamed.append((key, to, to not in d))
        d.setdefault(to, value)
    return renamed


def canonical_meal(data: dict, warnings: list[str]) -> None:
    """Meal input as agents write it, before validation: item nutrients under their own names.

    Only item nutrients count, and agents skip warnings, so a number on an item under a name
    the server does not know (energy, 热量, a nested nutrition object) is refused rather than
    stored and left out of the totals."""
    items = data.get("items")
    for i, item in enumerate(items if isinstance(items, list) else []):
        if not isinstance(item, dict):
            continue
        label = f"items[{i}] {item.get('name', '')}"
        for key, to, used in canonical_nutrients(item):
            warnings.append(f"{label}: read {key} as {to}; send {to} next time" if used
                            else f"{label}: dropped {key}, {to} is already given")
        for key, value in item.items():
            if key not in _MEAL_ITEM["properties"] and (
                    isinstance(value, dict) or isinstance(value, (int, float)) and not isinstance(value, bool)):
                raise ApiError(422, "invalid_data",
                               f"data['items'][{i}][{key!r}]: not a meal item field, so it would not count",
                               f"nutrients go on each item as {', '.join(NUTRIENTS)}; how much as "
                               "amount, unit and grams. GET /api/v1/categories/meal shows the schema")
    for key in [k for k, v in data.items()
                if _nutrient_key(k.lower().removeprefix("total_").removesuffix("_total"), v)]:
        del data[key]
        warnings.append(f"dropped data.{key}: only the items' nutrients count, and the server sums "
                        "them into data.totals")


# Units that measure weight or volume, as (g or ml, how many of those one is).
MEASURES = {"g": ("g", 1), "gram": ("g", 1), "grams": ("g", 1), "克": ("g", 1), "kg": ("g", 1000),
            "公斤": ("g", 1000), "千克": ("g", 1000), "斤": ("g", 500), "两": ("g", 50),
            "ml": ("ml", 1), "毫升": ("ml", 1), "cc": ("ml", 1), "l": ("ml", 1000), "升": ("ml", 1000)}
# Units that count servings of a library food, whatever its own serving unit is.
SERVING_UNITS = {"份", "serving", "servings", "portion", "portions"}
_FOOD_BASIS = ("food_id", "amount", "unit", "grams")
_NUMBERS = ("grams", *NUTRIENTS)  # what an item's amount decides, and an edit carries over


def _unit(u) -> str | None:
    return u.strip().lower() if isinstance(u, str) else None


def _portion(item: dict, food: dict) -> tuple[str, float] | str:
    """How much of `food` the item's amount says it is: ("servings", n) or ("g", grams); or why
    that can't be told. The amount comes before the item's grams, because it is what the user
    said ("3枚"); grams beside it are often the agent's own estimate, and decide only when the
    amount can't be matched with the food."""
    serving = food.get("serving") or {}
    s_amount, s_unit = serving.get("amount") or 1, serving.get("unit")
    amount, unit, grams = item.get("amount"), item.get("unit"), item.get("grams")
    u, su = _unit(unit), _unit(s_unit)
    measure = MEASURES.get(u or "")
    if amount is None:
        if u is not None and u not in SERVING_UNITS and not measure:
            amount = 1  # "一袋" is one of that unit, matched like any other amount
        elif grams is not None and u not in SERVING_UNITS:
            return "g", grams
        elif measure:
            return f"unit {unit!r} needs an amount: send amount, or grams"
        else:
            return "servings", 1
    if u is None:
        if s_amount == 1 or su is None:
            return "servings", amount
        if grams is not None:
            return "g", grams
        # 1 could be one 400 g tub or 1 g of it; reading it as the serving unit once made a tub 0.4 kcal.
        if su in SERVING_UNITS:
            return f"amount {amount:g} has no unit; a serving of this food is {s_amount:g} {s_unit}: send unit {s_unit!r}"
        return (f"amount {amount:g} has no unit, and a serving of this food is {s_amount:g} {s_unit}: "
                f"send unit '份' to count servings, or {s_unit!r} for an amount in {s_unit}")
    if u == su:  # before any conversion: a bakery's 1 斤 of bread is the 340 g it says, not 500 g
        return "servings", amount / s_amount
    if measure and measure[0] == "g":
        return "g", amount * measure[1]
    s_measure = MEASURES.get(su or "")
    if measure and s_measure and measure[0] == s_measure[0]:
        return "servings", amount * measure[1] / s_measure[1] / s_amount
    if u in SERVING_UNITS:
        return "servings", amount
    if grams is not None:
        return "g", grams
    if su is None:
        return f"{amount:g} {unit} cannot be matched with this food, which has nutrients per 100 g: send grams"
    return (f"{amount:g} {unit} cannot be matched with this food's serving ({s_amount:g} {s_unit}): "
            f"send grams, an amount in {s_unit!r}, or unit '份' for servings")


def _for_portion(kind: str, n: float, food: dict) -> tuple[float | None, dict] | str:
    """The grams (when known) and nutrients of `n` servings or grams of `food`."""
    serving = food.get("serving") or {}
    s_measure = MEASURES.get(_unit(serving.get("unit")) or "")
    s_grams = serving.get("grams") or (
        (serving.get("amount") or 1) * s_measure[1] if s_measure and s_measure[0] == "g" else None)
    grams = n if kind == "g" else n * s_grams if s_grams else None
    if food.get("per_100g") and grams is not None:
        return grams, _scaled(food["per_100g"], grams / 100)
    if food.get("per_serving") and kind == "servings":
        return grams, _scaled(food["per_serving"], n)
    if food.get("per_serving") and s_grams:
        return grams, _scaled(food["per_serving"], n / s_grams)
    if kind == "g":
        return (f"{n:g} g cannot be matched with this food: its serving has no weight; send an amount "
                f"in {serving.get('unit')!r}, or unit '份' for servings")
    return "this food has nutrients per 100 g and no weight for a serving: send grams"


def fill_from_food(item: dict, food: dict) -> tuple[dict, str | None, str | None]:
    """Fill an item's missing grams and nutrients from a /foods entry.

    Returns what was filled; why nothing could be, when the amount can't be matched with the
    food; and a note when the item's own grams disagree with its amount."""
    portion = _portion(item, food)
    by_amount = not isinstance(portion, str) and portion != ("g", item.get("grams"))
    found = portion if isinstance(portion, str) else _for_portion(*portion, food)
    if isinstance(found, str) and item.get("grams") is not None and portion != ("g", item["grams"]):
        found, by_amount = _for_portion("g", item["grams"], food), False  # the amount alone can't say
    if isinstance(found, str):
        return {}, found, None
    grams, nutrients = found
    filled = {}
    # A bare amount counts servings only when a serving is one unit, so that unit is the item's.
    unit = (food.get("serving") or {}).get("unit") if (
        by_amount and portion[0] == "servings" and item.get("amount") is not None) else None
    for k, v in {"grams": _round(grams) if grams is not None else None, "unit": unit,
                 **nutrients}.items():
        if v is not None and item.get(k) is None:
            item[k] = filled[k] = v
    item.setdefault("estimated", food.get("source") == "estimate")
    note = None
    if by_amount and grams and "grams" not in filled and abs(item["grams"] - grams) > 0.15 * grams:
        how = " ".join(f"{x:g}" if isinstance(x, (int, float)) else str(x)
                       for x in (item.get("amount", 1), item.get("unit") or "份"))
        note = (f"grams {item['grams']:g} do not match {how} ({_round(grams):g} g)"
                + ("; the nutrients follow the amount" if "kcal" in filled else ""))
    return filled, None, note


def _carried(item: dict, old: dict) -> dict:
    """The numbers `item` brings back unchanged from the stored item `old`."""
    return {k: old[k] for k in _NUMBERS
            if isinstance(old.get(k), (int, float)) and item.get(k) == old[k]}


def _stale_copy(item: dict, old: dict) -> bool:
    """Whether `item` is an older read of `old` sent again: same food and amount, every number
    off by the one factor an earlier edit scaled them by (a retried PATCH or upsert)."""
    if any(item.get(k) != old.get(k) for k in ("food_id", "amount", "unit")):
        return False
    pairs = [(item[k], old[k]) for k in _NUMBERS
             if isinstance(item.get(k), (int, float)) and isinstance(old.get(k), (int, float))]
    ratios = [o / n for n, o in pairs if n >= 1]
    return bool(ratios) and all(abs(r - ratios[0]) <= 0.05 * ratios[0] for r in ratios) \
        and abs(ratios[0] - 1) > 0.05


def _twins(sent: list, stored: list[dict]) -> tuple[set[int], dict[int, dict]]:
    """Which sent items are stored items sent back as they were, and which stored item each
    other food item is an edit of: the unclaimed one it still carries numbers from, preferring
    the same food (a food swap may come with a new name), then more numbers, then its place.
    Worked out before anything is filled, each stored item claimed once."""
    free = list(range(len(stored)))
    unchanged = set()
    for i, item in enumerate(sent):
        k = next((k for k in free if item == stored[k]), None)
        if k is not None:
            unchanged.add(i)
            free.remove(k)
    twins = {}
    for i, item in enumerate(sent):
        if i in unchanged or not isinstance(item, dict) or not item.get("food_id"):
            continue
        pool = [k for k in free if stored[k].get("food_id")]
        scored = [((stored[k]["food_id"] == item["food_id"], len(_carried(item, stored[k])), k == i), k)
                  for k in pool if _carried(item, stored[k]) or _stale_copy(item, stored[k])]
        if scored:
            k = max(scored)[1]
            twins[i] = stored[k]
            free.remove(k)
    return unchanged, twins


def _drop_carried_over(item: dict, old: dict, food: dict | None) -> dict:
    """An item sent back with a new amount, unit or food still carries the stored grams and
    nutrients, which held for the old amount. With the same food and unit and a new amount,
    they are scaled to it, whoever set them; a stale copy of a scaled item gets the stored
    numbers back. Otherwise those that match what the stored item's food gives for the old
    amount are removed, to be filled again, and numbers that never matched it stay; grams
    that were what the old amount went by stay too, while amount and unit do. Returns what
    was removed."""
    if _stale_copy(item, old):
        item.update({k: old[k] for k in _NUMBERS if k in old})
        return {}
    if all(item.get(k) == old.get(k) for k in _FOOD_BASIS):
        return {}
    carried = _carried(item, old)
    a, b = old.get("amount"), item.get("amount")
    if (item.get("food_id") == old.get("food_id") and _unit(item.get("unit")) == _unit(old.get("unit"))
            and isinstance(a, (int, float)) and isinstance(b, (int, float)) and a > 0 and a != b):
        for k, v in carried.items():
            item[k] = _round(v * b / a)
        return {}
    if food is None:
        return {}
    if (item.get("food_id") == old.get("food_id") and a == b
            and isinstance(_portion({"amount": b, "unit": item.get("unit")}, food), str)):
        return {}  # the unit renamed to one the food doesn't know (1 枚 -> 1 包): the numbers stand
    then = {k: old[k] for k in ("amount", "unit") if old.get(k) is not None}
    by_grams = old.get("grams") is not None and (old.get("amount") is None or fill_from_food(dict(then), food)[1])
    if by_grams:
        then["grams"] = old["grams"]  # the old amount alone did not tell how much; its grams did
    fill_from_food(then, food)
    # Grams the old amount went by still say how much, unless the amount or unit changed too.
    keep_grams = by_grams and all(item.get(k) == old.get(k) for k in ("amount", "unit"))
    dropped = {k: v for k, v in carried.items() if not (keep_grams and k == "grams")
               and isinstance(then.get(k), (int, float)) and abs(then[k] - v) <= 0.06}
    for k in dropped:
        del item[k]
    return dropped


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


def derive(conn, category: str, data: dict, start, end, warnings: list[str],
           stored: dict | None = None) -> dict:
    """Server-side fields. `start`/`end` are datetimes (end may be None); `stored` is the data
    of the record an update changes."""
    if category == "meal":
        from .foods import get_food, touch_food
        if "meal_type" not in data:
            data["meal_type"] = _meal_type_for(start.hour, start.minute)
        totals: dict[str, float] = {}
        stored_items = [s for s in (stored or {}).get("items") or [] if isinstance(s, dict)]
        unchanged, twins = _twins(data.get("items", []), stored_items)
        for i, item in enumerate(data.get("items", [])):
            label = f"items[{i}] {item.get('name', '')}"
            # An item sent back as stored stays as stored, so a note edit never refills or refuses it.
            if item.get("food_id") and i not in unchanged:
                food = get_food(conn, item["food_id"])
                old, dropped = twins.get(i), {}
                if old is not None:
                    dropped = _drop_carried_over(item, old, food if food and old["food_id"] == food["id"]
                                                 else get_food(conn, old["food_id"], deleted=True))
                if food is None:
                    if "kcal" not in item:
                        raise ApiError(422, "invalid_data",
                                       f"data['items'][{i}]['food_id']: food {item['food_id']} not found",
                                       "GET /api/v1/foods?q=<name> finds it, or give the item its own kcal")
                    warnings.append(f"{label}: food_id {item['food_id']} not found")
                else:
                    _, problem, note = fill_from_food(item, food)
                    # A new unit the food doesn't know, with the same amount: the old grams still say how much.
                    if problem and "grams" in dropped and item.get("amount") == old.get("amount"):
                        item["grams"] = dropped["grams"]
                        _, problem, note = fill_from_food(item, food)
                    if problem and "kcal" not in item:
                        raise ApiError(422, "invalid_data", f"data['items'][{i}]: {problem}",
                                       f"GET /api/v1/foods/{food['id']} shows its serving")
                    warnings += [f"{label}: {w}" for w in (problem, note) if w]
                    if not any(all(s.get(k) == item.get(k) for k in ("food_id", "amount", "unit"))
                               for s in stored_items):
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
    elif category == "smoking" and isinstance(data.get("count"), float):
        data["count"] = int(data["count"])  # the schema already refused 1.5; keep 2.0 out of titles
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
    if category == "smoking" and "count" in data:
        kind = data.get("kind")
        return f"抽烟 {data['count']} 根" + (
            f"（{SMOKING_KINDS[kind]}）" if kind in SMOKING_KINDS and kind != "cigarette" else "")
    for key in ("name", "text", "merchant"):
        if isinstance(data.get(key), str) and data[key]:
            return f"{cat_title}：{data[key][:40]}"
    return cat_title


# Workout fields under the names agents write, matched without case, as (key, factor into the
# key's unit). The page and /summary read only the schema's keys, so these would be stored and
# never shown.
WORKOUT_ALIASES = {
    **dict.fromkeys(("calories", "kcal", "cal", "active_calories", "calories_burned",
                     "kcal_burned", "energy_kcal"), ("active_kcal", 1)),
    "total_calories": ("total_kcal", 1),
    **dict.fromkeys(("duration", "duration_minutes", "duration_mins", "minutes"),
                    ("duration_min", 1)),
    **dict.fromkeys(("duration_s", "duration_sec", "duration_seconds"), ("duration_min", 1 / 60)),
    **dict.fromkeys(("duration_h", "duration_hr", "duration_hours"), ("duration_min", 60)),
    **dict.fromkeys(("distance", "distance_kilometers", "distance_kilometres"), ("distance_km", 1)),
    **dict.fromkeys(("distance_m", "distance_meters", "distance_metres"), ("distance_km", 0.001)),
    **dict.fromkeys(("distance_mi", "distance_miles"), ("distance_km", 1.609344)),
    **dict.fromkeys(("avg_heart_rate", "average_heart_rate", "average_hr"), ("avg_hr", 1)),
    "max_heart_rate": ("max_hr", 1),
}


def canonical_workout(data: dict, warnings: list[str]) -> None:
    """Workout input as agents write it, before validation: calories, duration and distance under
    their own names are renamed to the schema's keys, converting seconds, hours, metres and miles;
    a key already given wins. A bare distance of 100 or more is refused, since it is more likely
    metres than kilometres."""
    for key in list(data):
        value = data[key]
        to, factor = WORKOUT_ALIASES.get(key.lower(), (None, 1))
        if to is None or value is None or isinstance(value, bool) or (
                isinstance(value, str) and not any(c.isdigit() for c in value)):
            continue
        del data[key]
        if to in data:
            warnings.append(f"dropped data.{key}, {to} is already given")
            continue
        number = isinstance(value, (int, float))
        if key.lower() == "distance" and number and value >= 100:
            raise ApiError(422, "invalid_data",
                           f"data[{key!r}]: {value:g} has no unit, and as km it is too far",
                           "send distance_km in kilometres, or distance_m in metres")
        data[to] = round(value * factor, 3) if number and factor != 1 else value
        warnings.append(f"read data.{key} as {to}"
                        + (f" = {data[to]:g}" if number and factor != 1 else "")
                        + f"; send {to} next time")
