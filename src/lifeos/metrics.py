"""Metric catalog for numeric samples (steps, heart rate, weight, sleep stages ...).

Names are snake_case. Apple HealthKit identifiers and Health Auto Export names map onto them;
anything unknown gets a metric created on the fly, so no data is dropped.
"""
import re

# name: (title, unit, aggregation, HealthKit identifier suffix, aliases)
CATALOG: dict[str, tuple[str, str, str, str | None, tuple[str, ...]]] = {
    "step_count": ("步数", "count", "sum", "StepCount", ()),
    "walking_running_distance": ("步行+跑步距离", "km", "sum", "DistanceWalkingRunning", ()),
    "cycling_distance": ("骑行距离", "km", "sum", "DistanceCycling", ()),
    "swimming_distance": ("游泳距离", "m", "sum", "DistanceSwimming", ()),
    "flights_climbed": ("爬楼层数", "count", "sum", "FlightsClimbed", ()),
    "active_energy": ("活动能量", "kcal", "sum", "ActiveEnergyBurned", ()),
    "basal_energy": ("静息能量", "kcal", "sum", "BasalEnergyBurned", ("basal_energy_burned",)),
    "exercise_time": ("锻炼时长", "min", "sum", "AppleExerciseTime", ("apple_exercise_time",)),
    "stand_time": ("站立时长", "min", "sum", "AppleStandTime", ("apple_stand_time",)),
    "stand_hour": ("站立小时", "count", "sum", None, ("apple_stand_hour",)),
    "time_in_daylight": ("日照时间", "min", "sum", "TimeInDaylight", ()),
    "mindful_minutes": ("正念", "min", "sum", None, ("mindful_session",)),
    "heart_rate": ("心率", "count/min", "avg", "HeartRate", ()),
    "resting_heart_rate": ("静息心率", "count/min", "avg", "RestingHeartRate", ()),
    "walking_heart_rate_average": ("步行平均心率", "count/min", "avg",
                                   "WalkingHeartRateAverage", ()),
    "heart_rate_variability": ("心率变异性", "ms", "avg", "HeartRateVariabilitySDNN",
                               ("heart_rate_variability_sdnn",)),
    "oxygen_saturation": ("血氧", "%", "avg", "OxygenSaturation", ("blood_oxygen_saturation",)),
    "respiratory_rate": ("呼吸频率", "count/min", "avg", "RespiratoryRate", ()),
    "vo2_max": ("最大摄氧量", "mL/min·kg", "avg", "VO2Max", ()),
    "walking_speed": ("步行速度", "km/hr", "avg", "WalkingSpeed", ()),
    "environmental_audio_exposure": ("环境音量", "dBASPL", "avg",
                                     "EnvironmentalAudioExposure", ()),
    "headphone_audio_exposure": ("耳机音量", "dBASPL", "avg", "HeadphoneAudioExposure", ()),
    "body_mass": ("体重", "kg", "last", "BodyMass", ("weight_body_mass", "weight")),
    "body_mass_index": ("BMI", "count", "last", "BodyMassIndex", ()),
    "body_fat_percentage": ("体脂率", "%", "last", "BodyFatPercentage", ()),
    "lean_body_mass": ("去脂体重", "kg", "last", "LeanBodyMass", ()),
    "height": ("身高", "cm", "last", "Height", ()),
    "waist_circumference": ("腰围", "cm", "last", "WaistCircumference", ()),
    "blood_pressure_systolic": ("收缩压", "mmHg", "avg", "BloodPressureSystolic", ()),
    "blood_pressure_diastolic": ("舒张压", "mmHg", "avg", "BloodPressureDiastolic", ()),
    "blood_glucose": ("血糖", "mg/dL", "avg", "BloodGlucose", ()),
    "body_temperature": ("体温", "degC", "avg", "BodyTemperature", ()),
    "dietary_water": ("饮水", "mL", "sum", "DietaryWater", ("water",)),
    "dietary_energy": ("饮食能量（其他 App）", "kcal", "sum", "DietaryEnergyConsumed", ()),
    "sleep_in_bed": ("卧床", "min", "sum", None, ()),
    "sleep_asleep": ("睡眠（未分期）", "min", "sum", None, ()),
    "sleep_core": ("核心睡眠", "min", "sum", None, ()),
    "sleep_deep": ("深度睡眠", "min", "sum", None, ()),
    "sleep_rem": ("快速眼动睡眠", "min", "sum", None, ()),
    "sleep_awake": ("睡眠中清醒", "min", "sum", None, ()),
}
SLEEP_ASLEEP = ("sleep_asleep", "sleep_core", "sleep_deep", "sleep_rem")
# Apple stores these as fractions (0.97) under unit "%"; we store percent (97).
PERCENT_FRACTION = {"oxygen_saturation", "body_fat_percentage"}
# Cumulative HealthKit quantity types not in the catalog still need sum aggregation.
_SUM_HINTS = ("Dietary", "Distance", "Count", "Energy", "Time", "FlightsClimbed")

ALIASES = {alias: name for name, row in CATALOG.items() for alias in row[4]}
BY_HK = {row[3]: name for name, row in CATALOG.items() if row[3]}

_UNITS = {
    ("kJ", "kcal"): 1 / 4.184, ("Cal", "kcal"): 1.0,
    ("lb", "kg"): 0.45359237, ("g", "kg"): 0.001,
    ("mi", "km"): 1.609344, ("m", "km"): 0.001, ("km", "m"): 1000.0, ("yd", "m"): 0.9144,
    ("ft", "cm"): 30.48, ("in", "cm"): 2.54, ("m", "cm"): 100.0, ("ft", "m"): 0.3048,
    ("mi", "m"): 1609.344, ("km/hr", "km/hr"): 1.0, ("mi/hr", "km/hr"): 1.609344,
    ("m/s", "km/hr"): 3.6, ("bpm", "count/min"): 1.0,
    ("hr", "min"): 60.0, ("s", "min"): 1 / 60, ("ms", "ms"): 1.0,
    ("ml", "mL"): 1.0, ("L", "mL"): 1000.0, ("fl_oz_us", "mL"): 29.5735,
    ("mmol<180.1558800000541>/L", "mg/dL"): 18.0156, ("mmol/L", "mg/dL"): 18.0156,
    ("degF", "degC"): None,
}


def snake(s: str) -> str:
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", s)
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def canonical(name: str) -> str:
    n = snake(name)
    return ALIASES.get(n, n)


def from_hk(identifier: str) -> str:
    for prefix in ("HKQuantityTypeIdentifier", "HKCategoryTypeIdentifier",
                   "HKDataTypeIdentifier"):
        if identifier.startswith(prefix):
            suffix = identifier[len(prefix):]
            return BY_HK.get(suffix) or snake(suffix)
    return snake(identifier)


def guess_aggregation(name: str, hk_suffix: str | None = None) -> str:
    probe = hk_suffix or name
    if name.startswith("dietary_") or any(h in probe for h in _SUM_HINTS):
        return "sum"
    return "avg"


def convert(metric: str, value: float, unit: str, target: str) -> tuple[float, str]:
    """Convert into the metric's catalog unit where we know how; otherwise keep as given."""
    if metric in PERCENT_FRACTION and unit == "%" and value <= 1.0:
        return value * 100, "%"
    if not unit or unit == target:
        return value, target or unit
    if (unit, target) == ("degF", "degC"):
        return (value - 32) * 5 / 9, target
    factor = _UNITS.get((unit, target))
    if factor is None:
        return value, unit
    return value * factor, target


def seed(conn, now: str) -> None:
    for name, (title, unit, agg, _hk, _aliases) in CATALOG.items():
        conn.execute(
            "INSERT INTO metrics(name, title, unit, aggregation, date_by, builtin, created_at)"
            " VALUES (?, ?, ?, ?, ?, 1, ?) ON CONFLICT(name) DO UPDATE SET title = excluded.title,"
            " unit = excluded.unit, aggregation = excluded.aggregation,"
            " date_by = excluded.date_by, builtin = 1",
            (name, title, unit, agg, "end" if name.startswith("sleep_") else "start", now))
