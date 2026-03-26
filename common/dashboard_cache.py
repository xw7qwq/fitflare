from __future__ import annotations

import csv
import json
import math
import os
from datetime import datetime
from typing import Any

from common.fitbit_scopes import FITBIT_DASHBOARD_SCOPES, FITBIT_SCOPE_LABELS
from common.profile_paths import (
    cache_path_for,
    client_credentials_file_for,
    csv_path_for,
    ensure_dirs_for_cache,
    list_profiles,
    tokens_file_for,
)


DATASET_FILES = {
    "activity": "fitbit_activity.csv",
    "sleep": "fitbit_sleep.csv",
    "hrv": "fitbit_hrv.csv",
    "rhr": "fitbit_rhr.csv",
}

SNAPSHOT_FILENAME = "fitbit_profile_snapshot.json"
DASHBOARD_FILENAME = "dashboard.json"

GROUP_LABELS = {
    "account": "账户",
    "activity": "活动",
    "body": "体征",
    "nutrition": "生活",
    "sleep": "睡眠",
    "vitals": "生命体征",
}


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _read_json(path: str) -> dict[str, Any]:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_json(path: str, payload: dict[str, Any]) -> None:
    ensure_dirs_for_cache(path)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def _read_csv_rows(path: str) -> list[dict[str, str]]:
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            return [dict(row) for row in reader]
    except Exception:
        return []


def _date_only(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    return None


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null", "nat"}:
        return None
    try:
        return float(text)
    except Exception:
        return None


def _to_int(value: Any) -> int | None:
    number = _to_float(value)
    if number is None:
        return None
    return int(round(number))


def _to_bool(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _round(value: float | None, digits: int = 1) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


def _mean(values: list[float | int | None]) -> float | None:
    clean = [float(value) for value in values if value is not None]
    if not clean:
        return None
    return sum(clean) / len(clean)


def _median(values: list[float | int | None]) -> float | None:
    clean = sorted(float(value) for value in values if value is not None)
    if not clean:
        return None
    mid = len(clean) // 2
    if len(clean) % 2:
        return clean[mid]
    return (clean[mid - 1] + clean[mid]) / 2


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _safe_percent(delta: float | None, base: float | None) -> float | None:
    if delta is None or base in (None, 0):
        return None
    try:
        return (delta / abs(base)) * 100.0
    except Exception:
        return None


def _latest_row(rows: list[dict[str, Any]], field: str) -> dict[str, Any] | None:
    for row in reversed(rows):
        if row.get(field) is not None:
            return row
    return None


def _window_values(
    rows: list[dict[str, Any]],
    field: str,
    size: int,
    offset: int = 0,
) -> list[float]:
    values: list[float] = []
    for row in reversed(rows):
        if offset > 0:
            offset -= 1
            continue
        value = row.get(field)
        if value is None:
            continue
        values.append(float(value))
        if len(values) >= size:
            break
    values.reverse()
    return values


def _build_trend(
    rows: list[dict[str, Any]],
    field: str,
    higher_is_better: bool,
    tolerance: float,
) -> dict[str, Any]:
    current = _mean(_window_values(rows, field, size=7))
    previous = _mean(_window_values(rows, field, size=7, offset=7))
    if current is None or previous is None:
        return {
            "direction": "flat",
            "delta": None,
            "percent": None,
        }
    delta = current - previous
    if abs(delta) <= tolerance:
        direction = "flat"
    elif delta > 0:
        direction = "up" if higher_is_better else "down"
    else:
        direction = "down" if higher_is_better else "up"
    return {
        "direction": direction,
        "delta": _round(delta, 1),
        "percent": _round(_safe_percent(delta, previous), 1),
    }


def _dataset_meta(rows: list[dict[str, Any]], date_field: str = "date") -> dict[str, Any]:
    dates = [row.get(date_field) for row in rows if row.get(date_field)]
    if not dates:
        return {"count": 0, "start_date": None, "end_date": None}
    dates = sorted(str(date_value) for date_value in dates)
    return {
        "count": len(rows),
        "start_date": dates[0],
        "end_date": dates[-1],
    }


def _parse_activity_rows(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    parsed: list[dict[str, Any]] = []
    for row in rows:
        date = _date_only(row.get("date"))
        if not date:
            continue
        parsed.append(
            {
                "date": date,
                "steps": _to_int(row.get("steps")),
                "sedentary_minutes": _to_int(row.get("sedentaryMinutes")),
                "activity_calories": _to_int(row.get("activityCalories")),
                "calories_out": _to_int(row.get("caloriesOut")),
                "lightly_active_minutes": _to_int(row.get("lightlyActiveMinutes")),
                "fairly_active_minutes": _to_int(row.get("fairlyActiveMinutes")),
                "very_active_minutes": _to_int(row.get("veryActiveMinutes")),
                "active_zone_minutes": _to_int(row.get("activeZoneMinutes")),
                "active_minutes": _to_int(row.get("activeMinutes")),
                "exercise_examples": (row.get("exerciseExamples") or "").strip() or None,
            }
        )
    parsed.sort(key=lambda item: item["date"])
    return parsed


def _select_sleep_rows(rows: list[dict[str, str]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    groups: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        date = _date_only(row.get("date"))
        if not date:
            continue
        groups.setdefault(date, []).append(row)

    selected: list[dict[str, Any]] = []
    nap_counts: dict[str, int] = {}
    for date in sorted(groups.keys()):
        entries = groups[date]
        nap_counts[date] = max(len(entries) - 1, 0)
        entries.sort(
            key=lambda item: (
                1 if _to_bool(item.get("isMainSleep")) else 0,
                _to_int(item.get("minutesAsleep")) or 0,
                _to_int(item.get("duration")) or 0,
            ),
            reverse=True,
        )
        winner = entries[0]
        selected.append(
            {
                "date": date,
                "is_main_sleep": _to_bool(winner.get("isMainSleep")),
                "minutes_asleep": _to_int(winner.get("minutesAsleep")),
                "minutes_awake": _to_int(winner.get("minutesAwake")),
                "time_in_bed": _to_int(winner.get("timeInBed")),
                "efficiency": _to_int(winner.get("efficiency")),
                "sleep_score": _to_float(winner.get("sleepScore")),
                "minutes_deep": _to_int(winner.get("minutesDeep")),
                "minutes_rem": _to_int(winner.get("minutesREM")),
                "minutes_light": _to_int(winner.get("minutesLight")),
                "minutes_wake_stages": _to_int(winner.get("minutesWakeStages")),
                "minutes_to_fall_asleep": _to_int(winner.get("minutesToFallAsleep")),
                "minutes_after_wakeup": _to_int(winner.get("minutesAfterWakeup")),
                "start_time": (winner.get("startTime") or "").strip() or None,
                "end_time": (winner.get("endTime") or "").strip() or None,
            }
        )
    return selected, nap_counts


def _parse_hrv_rows(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    parsed: list[dict[str, Any]] = []
    for row in rows:
        date = _date_only(row.get("date"))
        if not date:
            continue
        parsed.append(
            {
                "date": date,
                "hrv": _to_float(row.get("dailyRmssd")),
                "deep_rmssd": _to_float(row.get("deepRmssd")),
            }
        )
    parsed.sort(key=lambda item: item["date"])
    return parsed


def _parse_rhr_rows(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    parsed: list[dict[str, Any]] = []
    for row in rows:
        date = _date_only(row.get("date"))
        if not date:
            continue
        parsed.append(
            {
                "date": date,
                "rhr": _to_int(row.get("resting_heart_rate")),
            }
        )
    parsed.sort(key=lambda item: item["date"])
    return parsed


def _merge_daily_rows(
    activity_rows: list[dict[str, Any]],
    sleep_rows: list[dict[str, Any]],
    hrv_rows: list[dict[str, Any]],
    rhr_rows: list[dict[str, Any]],
    nap_counts: dict[str, int],
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}

    for rows in (activity_rows, sleep_rows, hrv_rows, rhr_rows):
        for row in rows:
            bucket = merged.setdefault(row["date"], {"date": row["date"]})
            bucket.update(row)

    for date, count in nap_counts.items():
        bucket = merged.setdefault(date, {"date": date})
        bucket["nap_count"] = count

    combined = [merged[key] for key in sorted(merged.keys())]
    for row in combined:
        minutes_asleep = row.get("minutes_asleep")
        if minutes_asleep is not None:
            row["sleep_hours"] = _round(minutes_asleep / 60.0, 1)
        deep = row.get("minutes_deep")
        rem = row.get("minutes_rem")
        total = row.get("minutes_asleep")
        if total:
            if deep is not None:
                row["deep_pct"] = _round((deep / total) * 100.0, 1)
            if rem is not None:
                row["rem_pct"] = _round((rem / total) * 100.0, 1)
    return combined


def _aggregate_rows(rows: list[dict[str, Any]], period: str) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        date = row.get("date")
        if not date:
            continue
        parsed = datetime.strptime(date, "%Y-%m-%d")
        if period == "week":
            year, week, _weekday = parsed.isocalendar()
            key = f"{year}-W{week:02d}"
        else:
            key = parsed.strftime("%Y-%m")
        buckets.setdefault(key, []).append(row)

    aggregated: list[dict[str, Any]] = []
    for key in sorted(buckets.keys()):
        bucket = buckets[key]
        aggregated.append(
            {
                "period": key,
                "days": len(bucket),
                "sleep_score": _round(_mean([row.get("sleep_score") for row in bucket]), 1),
                "sleep_hours": _round(_mean([row.get("sleep_hours") for row in bucket]), 1),
                "steps": _round(_mean([row.get("steps") for row in bucket]), 0),
                "active_minutes": _round(_mean([row.get("active_minutes") for row in bucket]), 0),
                "active_zone_minutes": _round(_mean([row.get("active_zone_minutes") for row in bucket]), 0),
                "hrv": _round(_mean([row.get("hrv") for row in bucket]), 1),
                "rhr": _round(_mean([row.get("rhr") for row in bucket]), 1),
                "calories_out": _round(_mean([row.get("calories_out") for row in bucket]), 0),
            }
        )
    return aggregated


def _pearson(x_values: list[float], y_values: list[float]) -> float | None:
    if len(x_values) < 3 or len(x_values) != len(y_values):
        return None
    mean_x = sum(x_values) / len(x_values)
    mean_y = sum(y_values) / len(y_values)
    numerator = 0.0
    denom_x = 0.0
    denom_y = 0.0
    for x_value, y_value in zip(x_values, y_values):
        dx = x_value - mean_x
        dy = y_value - mean_y
        numerator += dx * dy
        denom_x += dx * dx
        denom_y += dy * dy
    if denom_x <= 0 or denom_y <= 0:
        return None
    return numerator / math.sqrt(denom_x * denom_y)


def _build_correlation(rows: list[dict[str, Any]], x_field: str, y_field: str, label: str) -> dict[str, Any]:
    pairs: list[tuple[float, float]] = []
    for row in rows:
        x_value = row.get(x_field)
        y_value = row.get(y_field)
        if x_value is None or y_value is None:
            continue
        pairs.append((float(x_value), float(y_value)))

    coefficient = _pearson([pair[0] for pair in pairs], [pair[1] for pair in pairs])
    strength = "数据不足"
    if coefficient is not None:
        absolute = abs(coefficient)
        if absolute >= 0.7:
            strength = "强相关"
        elif absolute >= 0.4:
            strength = "中等相关"
        elif absolute >= 0.2:
            strength = "弱相关"
        else:
            strength = "接近无相关"
    return {
        "label": label,
        "points": len(pairs),
        "coefficient": _round(coefficient, 3),
        "strength": strength,
    }


def _recovery_score(rows: list[dict[str, Any]], sleep_goal_minutes: int | None) -> dict[str, Any]:
    if not rows:
        return {"score": None, "label": "等待数据"}

    latest = rows[-1]
    baseline_hrv = _median([row.get("hrv") for row in rows[-30:]])
    baseline_rhr = _median([row.get("rhr") for row in rows[-30:]])

    components: list[float] = []

    sleep_score = latest.get("sleep_score")
    if sleep_score is not None:
        components.append(_clamp(float(sleep_score), 0.0, 100.0))

    minutes_asleep = latest.get("minutes_asleep")
    if minutes_asleep is not None and sleep_goal_minutes:
        components.append(_clamp((float(minutes_asleep) / float(sleep_goal_minutes)) * 100.0, 0.0, 100.0))

    hrv = latest.get("hrv")
    if hrv is not None and baseline_hrv:
        components.append(_clamp(50.0 + ((float(hrv) - baseline_hrv) / baseline_hrv) * 100.0, 0.0, 100.0))

    rhr = latest.get("rhr")
    if rhr is not None and baseline_rhr:
        components.append(_clamp(50.0 - (float(rhr) - baseline_rhr) * 6.0, 0.0, 100.0))

    score = _round(_mean(components), 0)
    if score is None:
        return {"score": None, "label": "等待数据"}
    if score >= 80:
        label = "恢复良好"
    elif score >= 65:
        label = "恢复稳定"
    elif score >= 50:
        label = "建议观察"
    else:
        label = "需要休息"
    return {"score": int(score), "label": label}


def _endpoint_entry(snapshot: dict[str, Any], key: str) -> dict[str, Any]:
    entry = (snapshot.get("endpoints") or {}).get(key) or {}
    return entry if isinstance(entry, dict) else {}


def _endpoint_data(snapshot: dict[str, Any], key: str) -> Any:
    entry = _endpoint_entry(snapshot, key)
    if not entry.get("ok"):
        return None
    return entry.get("data")


def _token_metadata(profile_id: str | None) -> dict[str, Any]:
    tokens = _read_json(tokens_file_for(profile_id))
    scope_text = str(tokens.get("scope", "")).strip()
    return {
        "scopes": [part for part in scope_text.split() if part],
        "user_id": tokens.get("user_id"),
    }


def _collect_records(payload: Any, preferred_keys: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in preferred_keys:
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        for value in payload.values():
            if isinstance(value, list) and all(isinstance(item, dict) for item in value):
                return [item for item in value if isinstance(item, dict)]
        return [payload] if any(isinstance(value, (dict, list, int, float, str)) for value in payload.values()) else []
    return []


def _record_date(record: dict[str, Any]) -> str | None:
    for key in ("date", "dateTime", "logDate"):
        date = _date_only(record.get(key))
        if date:
            return date
    return None


def _flatten_numeric_leaves(value: Any, prefix: str = "") -> list[tuple[str, float]]:
    items: list[tuple[str, float]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            items.extend(_flatten_numeric_leaves(child, child_prefix))
    elif isinstance(value, list):
        for index, child in enumerate(value[:3]):
            child_prefix = f"{prefix}[{index}]" if prefix else f"[{index}]"
            items.extend(_flatten_numeric_leaves(child, child_prefix))
    else:
        numeric = _to_float(value)
        if numeric is not None:
            items.append((prefix or "value", numeric))
    return items


def _pick_numeric_leaf(payload: Any, preferred_paths: tuple[str, ...]) -> tuple[str | None, float | None]:
    leaves = _flatten_numeric_leaves(payload)
    if not leaves:
        return None, None
    for preferred in preferred_paths:
        for key, value in leaves:
            if key == preferred or key.endswith(f".{preferred}"):
                return key, value
    return leaves[0]


def _deep_get(payload: Any, *path: str) -> Any:
    current = payload
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _join_non_empty(parts: list[str | None], separator: str = " · ") -> str:
    clean = [str(part).strip() for part in parts if str(part or "").strip()]
    return separator.join(clean)


def _distance_value(entries: Any, activity_name: str = "total") -> float | None:
    if not isinstance(entries, list):
        return None
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("activity") or "").strip().lower() != activity_name.lower():
            continue
        value = _to_float(entry.get("distance"))
        if value is not None:
            return value
    return None


def _duration_minutes(value: Any) -> int | None:
    number = _to_float(value)
    if number is None:
        return None
    if number > 1000:
        return int(round(number / 60000.0))
    return int(round(number))


def _lifetime_total_value(payload: Any, key: str) -> float | None:
    total = _deep_get(payload, "lifetime", "total")
    if not isinstance(total, dict):
        return None
    if key == "distance":
        direct = _to_float(total.get("distance"))
        if direct is not None:
            return direct
        return _distance_value(total.get("distance"), "total")
    return _to_float(total.get(key))


def _latest_record_summary(
    payload: Any,
    preferred_keys: tuple[str, ...],
    preferred_numeric: tuple[str, ...],
) -> dict[str, Any]:
    records = _collect_records(payload, preferred_keys)
    if not records:
        return {}
    records.sort(key=lambda record: _record_date(record) or "")
    latest = records[-1]
    key, value = _pick_numeric_leaf(latest, preferred_numeric)
    return {
        "date": _record_date(latest),
        "field": key,
        "value": value,
        "record": latest,
        "count": len(records),
    }


def _pretty_leaf_label(path: str | None) -> str:
    if not path:
        return "值"
    leaf = path.split(".")[-1]
    leaf = leaf.split("[")[0]
    mapping = {
        "avg": "平均值",
        "average": "平均值",
        "bmi": "BMI",
        "breathingRate": "呼吸率",
        "calories": "热量",
        "distance": "距离",
        "fat": "体脂率",
        "nightlyRelative": "相对皮温",
        "spo2": "SpO2",
        "temperature": "温度",
        "value": "值",
        "weight": "体重",
    }
    return mapping.get(leaf, leaf)


def _value_card(
    label: str,
    value: Any,
    unit: str | None,
    detail: str,
    tone: str,
    hint: str | None = None,
) -> dict[str, Any]:
    return {
        "label": label,
        "value": value,
        "unit": unit,
        "detail": detail,
        "tone": tone,
        "hint": hint,
    }


def _calc_bmi(weight_kg: float | None, height_cm: float | None) -> float | None:
    if weight_kg is None or height_cm in (None, 0):
        return None
    try:
        meters = float(height_cm) / 100.0
        return _round(float(weight_kg) / (meters * meters), 1)
    except Exception:
        return None


def _parse_goal_value(payload: Any, preferred_keys: tuple[str, ...]) -> float | None:
    if isinstance(payload, dict):
        goal = payload.get("goal")
        if isinstance(goal, dict):
            for key in preferred_keys:
                value = _to_float(goal.get(key))
                if value is not None:
                    return value
        for key in preferred_keys:
            value = _to_float(payload.get(key))
            if value is not None:
                return value
    return None


def _build_body_tables(profile: dict[str, Any], snapshot: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    weight_goal_payload = _endpoint_data(snapshot, "weight_goal") or {}
    fat_goal_payload = _endpoint_data(snapshot, "fat_goal") or {}
    weight_records = _collect_records(_endpoint_data(snapshot, "weight_log_recent"), ("weight",))
    fat_records = _collect_records(_endpoint_data(snapshot, "fat_log_recent"), ("fat",))

    weight_records.sort(key=lambda record: _record_date(record) or "")
    fat_records.sort(key=lambda record: _record_date(record) or "")

    latest_weight = weight_records[-1] if weight_records else {}
    latest_fat = fat_records[-1] if fat_records else {}

    current_weight = _to_float(latest_weight.get("weight")) or profile.get("weight_kg")
    weight_bmi = _to_float(latest_weight.get("bmi")) or _calc_bmi(current_weight, profile.get("height_cm"))
    weight_goal = _parse_goal_value(weight_goal_payload, ("weight", "goalWeight", "targetWeight"))
    fat_goal = _parse_goal_value(fat_goal_payload, ("fat", "goalFat", "targetFat"))
    latest_fat_pct = _to_float(latest_fat.get("fat")) or _to_float(latest_fat.get("value"))

    metrics = [
        _value_card(
            "当前体重",
            _round(current_weight, 1),
            "kg",
            f"最近记录：{_record_date(latest_weight) or profile.get('last_snapshot_at') or '暂无'}",
            "blue",
            "优先读体重日志，没有日志时退回 profile 快照。",
        ),
        _value_card(
            "BMI",
            _round(weight_bmi, 1),
            None,
            "由最近体重和个人身高估算。",
            "teal",
        ),
        _value_card(
            "体重目标",
            _round(weight_goal, 1),
            "kg",
            "读取 Fitbit 体重目标设置。",
            "green",
        ),
        _value_card(
            "体脂率",
            _round(latest_fat_pct, 1),
            "%",
            f"最近记录：{_record_date(latest_fat) or '暂无'}",
            "amber",
        ),
    ]

    rows: list[dict[str, Any]] = []
    for row in reversed(weight_records[-8:]):
        rows.append({
            "date": _record_date(row),
            "type": "体重",
            "value": _round(_to_float(row.get("weight")), 1),
            "unit": "kg",
            "detail": f"BMI {_round(_to_float(row.get('bmi')), 1) or '--'} · 来源 {row.get('source') or 'Fitbit'}",
        })
    for row in reversed(fat_records[-8:]):
        rows.append({
            "date": _record_date(row),
            "type": "体脂",
            "value": _round(_to_float(row.get("fat")) or _to_float(row.get("value")), 1),
            "unit": "%",
            "detail": f"来源 {row.get('source') or 'Fitbit'}",
        })
    rows.sort(key=lambda row: (row.get("date") or "", row.get("type") or ""), reverse=True)
    return {"metrics": metrics}, rows[:12]


def _activity_log_date(record: dict[str, Any]) -> str | None:
    return (
        _date_only(record.get("startTime"))
        or _date_only(record.get("originalStartTime"))
        or _record_date(record)
    )


def _build_activity_tables(snapshot: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    today_payload = _endpoint_data(snapshot, "today_activity_summary") or {}
    summary = today_payload.get("summary") or {}
    goals = today_payload.get("goals") or {}
    lifetime_payload = _endpoint_data(snapshot, "lifetime_stats") or {}
    activity_logs = _collect_records(_endpoint_data(snapshot, "activity_log_list"), ("activities",))
    recent_types = _collect_records(_endpoint_data(snapshot, "recent_activity_types"), ("recentActivities", "activities"))
    frequent_types = _collect_records(_endpoint_data(snapshot, "frequent_activity_types"), ("activities",))
    favorite_types = _collect_records(_endpoint_data(snapshot, "favorite_activity_types"), ("activities",))

    activity_logs.sort(
        key=lambda record: (
            _activity_log_date(record) or "",
            str(record.get("startTime") or record.get("originalStartTime") or ""),
        )
    )

    steps = _to_int(summary.get("steps"))
    steps_goal = _to_int(goals.get("steps"))
    steps_progress = None
    if steps is not None and steps_goal not in (None, 0):
        steps_progress = int(round((float(steps) / float(steps_goal)) * 100.0))

    latest_log = activity_logs[-1] if activity_logs else {}
    latest_name = latest_log.get("activityName") or latest_log.get("name")
    latest_date = _activity_log_date(latest_log)
    lifetime_steps = _to_int(_lifetime_total_value(lifetime_payload, "steps"))
    lifetime_distance = _round(_lifetime_total_value(lifetime_payload, "distance"), 1)
    total_distance = _round(_distance_value(summary.get("distances"), "total"), 1)
    calories_out = _to_int(summary.get("caloriesOut"))
    activity_calories = _to_int(summary.get("activityCalories"))
    resting_hr = _to_int(summary.get("restingHeartRate"))

    metrics = [
        _value_card(
            "今日步数达成",
            steps_progress,
            "%",
            f"{steps or 0} / {steps_goal or 0} 步",
            "green",
            "优先读取 Fitbit 今日活动摘要和目标设置。",
        ),
        _value_card(
            "今日距离",
            total_distance,
            "km",
            f"静息心率 {resting_hr if resting_hr is not None else '--'} bpm",
            "blue",
            "来自今日活动摘要 summary.distances.total。",
        ),
        _value_card(
            "活动热量",
            activity_calories,
            "kcal",
            f"今日总消耗 {calories_out if calories_out is not None else '--'} kcal",
            "amber",
            "把运动热量和总消耗分开缓存。",
        ),
        _value_card(
            "活动日志",
            len(activity_logs),
            "条",
            f"最近：{latest_name or '暂无'} · {latest_date or '暂无'}",
            "teal",
            f"最近 / 常做 / 收藏：{len(recent_types)} / {len(frequent_types)} / {len(favorite_types)}",
        ),
        _value_card(
            "终身步数",
            lifetime_steps,
            "步",
            f"终身距离 {lifetime_distance if lifetime_distance is not None else '--'} km",
            "red",
            "来自 Fitbit lifetime stats。",
        ),
    ]

    rows: list[dict[str, Any]] = []
    for record in sorted(
        activity_logs,
        key=lambda row: (
            _activity_log_date(row) or "",
            str(row.get("startTime") or row.get("originalStartTime") or ""),
        ),
        reverse=True,
    )[:12]:
        duration = _duration_minutes(record.get("duration") or record.get("originalDuration"))
        distance = _round(_to_float(record.get("distance")), 1)
        calories = _to_int(record.get("calories"))
        detail = _join_non_empty(
            [
                f"热量 {calories} kcal" if calories is not None else None,
                f"步数 {_to_int(record.get('steps'))}" if _to_int(record.get("steps")) is not None else None,
                "手动记录" if _to_bool(record.get("manualValuesSpecified")) else None,
                str(record.get("logType") or "").strip() or None,
            ]
        )
        rows.append(
            {
                "date": _activity_log_date(record),
                "name": record.get("activityName") or record.get("name") or record.get("description"),
                "duration": duration,
                "calories": calories,
                "distance": distance,
                "detail": detail or "Fitbit activity log",
            }
        )
    return {"metrics": metrics}, rows


def _build_vitals_tables(snapshot: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    specs = [
        ("spo2_recent", "血氧", "%", "blue", ("spo2", "avg", "value.avg")),
        ("breathing_rate_recent", "呼吸率", "次/分", "teal", ("breathingRate", "value.breathingRate", "avg")),
        ("skin_temperature_recent", "皮温", "°C", "amber", ("nightlyRelative", "value.nightlyRelative", "temperature")),
    ]
    metrics: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []

    for key, label, unit, tone, preferred_numeric in specs:
        entry = _endpoint_entry(snapshot, key)
        summary = _latest_record_summary(entry.get("data"), (), preferred_numeric)
        metrics.append(
            _value_card(
                label,
                _round(summary.get("value"), 1),
                unit,
                f"最近记录：{summary.get('date') or '暂无'} · 最近 {summary.get('count') or 0} 条",
                tone,
                _pretty_leaf_label(summary.get("field")),
            )
        )
        detail_parts = []
        for path, value in _flatten_numeric_leaves(summary.get("record"))[:3]:
            detail_parts.append(f"{_pretty_leaf_label(path)} {_round(value, 1)}")
        rows.append({
            "date": summary.get("date"),
            "metric": label,
            "value": _round(summary.get("value"), 1),
            "unit": unit,
            "detail": " · ".join(detail_parts) or "暂无可解析摘要",
        })
    return {"metrics": metrics}, rows


def _build_lifestyle_tables(snapshot: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    food_goal_payload = _endpoint_data(snapshot, "food_goal") or {}
    food_log_payload = _endpoint_data(snapshot, "food_log_today") or {}
    recent_foods = _collect_records(_endpoint_data(snapshot, "recent_foods"), ("foods",))
    frequent_foods = _collect_records(_endpoint_data(snapshot, "frequent_foods"), ("foods",))
    favorite_foods = _collect_records(_endpoint_data(snapshot, "favorite_foods"), ("foods",))
    meals = _collect_records(_endpoint_data(snapshot, "meals"), ("meals",))
    water_goal_payload = _endpoint_data(snapshot, "water_goal") or {}
    water_log_payload = _endpoint_data(snapshot, "water_log_today") or {}

    calorie_goal = _parse_goal_value(food_goal_payload, ("calories",))
    calorie_summary = _pick_numeric_leaf(food_log_payload, ("summary.calories", "calories", "summary.caloriesConsumed"))[1]
    water_goal = _parse_goal_value(water_goal_payload, ("goal", "water", "amount"))
    water_value = _pick_numeric_leaf(water_log_payload, ("summary.water", "water", "summary.amount"))[1]

    metrics = [
        _value_card("热量目标", _round(calorie_goal, 0), "kcal", "读取 Fitbit 营养目标。", "amber"),
        _value_card("今日摄入", _round(calorie_summary, 0), "kcal", "来自今日饮食日志摘要。", "red"),
        _value_card("饮水目标", _round(water_goal, 0), "ml", "读取 Fitbit 饮水目标。", "blue"),
        _value_card("今日饮水", _round(water_value, 0), "ml", f"近期食物 {len(recent_foods)} 条。", "teal"),
        _value_card("常吃食物", len(frequent_foods), "项", "Fitbit frequent foods 缓存。", "green"),
        _value_card("收藏食物", len(favorite_foods), "项", "Fitbit favorite foods 缓存。", "blue"),
        _value_card("餐食模板", len(meals), "个", "Fitbit meals 缓存。", "teal"),
    ]

    rows = [
        {
            "name": row.get("name") or row.get("foodName") or row.get("description"),
            "brand": row.get("brand") or row.get("brandName"),
            "calories": _round(_to_float(row.get("calories")), 0),
            "amount": _join_non_empty([
                str(row.get("amount")) if row.get("amount") is not None else None,
                (row.get("unit") or {}).get("name") if isinstance(row.get("unit"), dict) else row.get("amountUnit"),
            ], " "),
            "last_eaten": row.get("dateLastEaten"),
        }
        for row in recent_foods[:12]
    ]
    return {"metrics": metrics}, rows


def _build_account_tables(
    profile: dict[str, Any],
    snapshot: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    devices = _endpoint_data(snapshot, "devices")
    device_rows = [row for row in (devices or []) if isinstance(row, dict)]
    badges_payload = _endpoint_data(snapshot, "badges")
    badges = _collect_records(badges_payload, ("badges",))
    if not badges:
        profile_payload = _endpoint_data(snapshot, "profile") or {}
        user = profile_payload.get("user") or {}
        badges = [row for row in (user.get("topBadges") or []) if isinstance(row, dict)]
    alarm_groups = _endpoint_data(snapshot, "device_alarms")
    alarm_groups = [row for row in (alarm_groups or []) if isinstance(row, dict)]

    requested_scopes = snapshot.get("requested_scopes") or FITBIT_DASHBOARD_SCOPES
    granted_scopes = profile.get("scopes") or []
    missing_scopes = [scope for scope in requested_scopes if scope not in granted_scopes]
    endpoint_summary = snapshot.get("fetch_summary") or {}
    alarms_count = sum(len(group.get("alarms") or []) for group in alarm_groups)

    metrics = [
        _value_card("已配对设备", len(device_rows), "台", "读取 Fitbit 设备列表。", "blue"),
        _value_card("徽章数量", len(badges), "枚", "读取 Fitbit badge 资料。", "amber"),
        _value_card(
            "快照完成度",
            endpoint_summary.get("ok"),
            f"/{endpoint_summary.get('total') or 0}",
            f"跳过 {endpoint_summary.get('skipped') or 0} 项，失败 {endpoint_summary.get('failed') or 0} 项。",
            "green",
        ),
        _value_card("缺失 scope", len(missing_scopes), "项", "重新授权后可补全更多数据。", "red"),
    ]

    device_table = [
        {
            "device": row.get("deviceVersion") or row.get("id"),
            "type": row.get("type"),
            "battery": row.get("battery"),
            "last_sync": row.get("lastSyncTime"),
            "status": row.get("deviceVersion") or row.get("mac"),
        }
        for row in device_rows
    ]
    badge_table = [
        {
            "name": row.get("name"),
            "category": row.get("category"),
            "value": row.get("value"),
            "date": row.get("dateTime"),
        }
        for row in badges[:12]
    ]
    alarm_table = []
    for group in alarm_groups:
        for alarm in group.get("alarms") or []:
            if not isinstance(alarm, dict):
                continue
            alarm_table.append({
                "device": group.get("device_name"),
                "time": alarm.get("time"),
                "enabled": "开启" if alarm.get("enabled") else "关闭",
                "recurring": alarm.get("recurring") or alarm.get("weekDays") or "--",
            })

    endpoint_rows = []
    for key, entry in sorted((snapshot.get("endpoints") or {}).items()):
        if not isinstance(entry, dict):
            continue
        if entry.get("ok"):
            status = "已缓存"
        elif entry.get("skipped"):
            reason = str(entry.get("reason") or "")
            if reason.startswith("missing_scope:"):
                scope_key = reason.split(":", 1)[1]
                status = f"缺少 {FITBIT_SCOPE_LABELS.get(scope_key, scope_key)} scope"
            else:
                status = "已跳过"
        else:
            code = entry.get("status")
            status = f"失败 ({code})" if code else "失败"
        endpoint_rows.append({
            "dataset": entry.get("label") or key,
            "group": GROUP_LABELS.get(str(entry.get("group")), str(entry.get("group"))),
            "scope": FITBIT_SCOPE_LABELS.get(str(entry.get("scope")), str(entry.get("scope"))),
            "status": status,
            "updated_at": entry.get("fetched_at"),
        })

    section = {
        "metrics": metrics,
        "requested_scopes": requested_scopes,
        "missing_scopes": missing_scopes,
        "granted_scopes": granted_scopes,
        "alarms_count": alarms_count,
    }
    return section, device_table, badge_table, alarm_table, endpoint_rows


def _profile_from_snapshot(profile_id: str | None, snapshot: dict[str, Any]) -> dict[str, Any]:
    profile_payload = _endpoint_data(snapshot, "profile") or {}
    user = profile_payload.get("user") or {}
    sleep_goal_payload = _endpoint_data(snapshot, "sleep_goal") or {}
    activity_daily_goal = _endpoint_data(snapshot, "activity_goals_daily") or {}
    activity_weekly_goal = _endpoint_data(snapshot, "activity_goals_weekly") or {}
    badges = _collect_records(_endpoint_data(snapshot, "badges"), ("badges",))
    if not badges:
        badges = [row for row in (user.get("topBadges") or []) if isinstance(row, dict)]
    devices = _endpoint_data(snapshot, "devices") or []
    token_meta = _token_metadata(profile_id)

    sleep_goal_minutes = None
    goal = sleep_goal_payload.get("goal") or {}
    if isinstance(goal, dict):
        sleep_goal_minutes = _to_int(goal.get("minDuration"))

    activity_goals = activity_daily_goal.get("goals") or activity_daily_goal
    weekly_goals = activity_weekly_goal.get("goals") or activity_weekly_goal
    scopes = snapshot.get("token_scope") or token_meta.get("scopes") or []

    return {
        "id": profile_id,
        "display_name": user.get("displayName") or user.get("fullName") or profile_id,
        "full_name": user.get("fullName") or user.get("displayName") or profile_id,
        "avatar": user.get("avatar640") or user.get("avatar150") or user.get("avatar"),
        "member_since": user.get("memberSince"),
        "timezone": user.get("timezone"),
        "locale": user.get("locale"),
        "country": user.get("country"),
        "encoded_id": user.get("encodedId"),
        "height_cm": _to_float(user.get("height")),
        "weight_kg": _to_float(user.get("weight")),
        "sleep_goal_minutes": sleep_goal_minutes,
        "daily_steps_goal": _to_int(activity_goals.get("steps")) if isinstance(activity_goals, dict) else None,
        "daily_calories_goal": _to_int(activity_goals.get("caloriesOut")) if isinstance(activity_goals, dict) else None,
        "weekly_steps_goal": _to_int(weekly_goals.get("steps")) if isinstance(weekly_goals, dict) else None,
        "device_count": len(devices) if isinstance(devices, list) else 0,
        "badge_count": len(badges),
        "scopes": scopes,
        "requested_scopes": snapshot.get("requested_scopes") or FITBIT_DASHBOARD_SCOPES,
        "user_id": snapshot.get("token_user_id") or token_meta.get("user_id"),
        "last_snapshot_at": snapshot.get("saved_at"),
    }


def _metric_cards(rows: list[dict[str, Any]], profile: dict[str, Any]) -> list[dict[str, Any]]:
    specs = [
        ("sleep_score", "睡眠得分", "分", True, 1.0, "blue"),
        ("sleep_hours", "睡眠时长", "小时", True, 0.2, "amber"),
        ("steps", "步数", "步", True, 300.0, "green"),
        ("active_minutes", "活跃分钟", "分钟", True, 10.0, "teal"),
        ("active_zone_minutes", "燃脂区分钟", "分钟", True, 5.0, "red"),
        ("hrv", "HRV", "ms", True, 2.0, "blue"),
        ("rhr", "静息心率", "bpm", False, 1.0, "red"),
        ("calories_out", "消耗热量", "kcal", True, 40.0, "amber"),
    ]

    cards: list[dict[str, Any]] = []
    for field, label, unit, higher_is_better, tolerance, tone in specs:
        latest = _latest_row(rows, field)
        cards.append(
            {
                "key": field,
                "label": label,
                "unit": unit,
                "tone": tone,
                "latest": _round(latest.get(field), 1) if latest else None,
                "latest_date": latest.get("date") if latest else None,
                "avg7": _round(_mean(_window_values(rows, field, size=7)), 1),
                "avg30": _round(_mean(_window_values(rows, field, size=30)), 1),
                "goal": profile.get("sleep_goal_minutes") if field == "sleep_hours" else profile.get("daily_steps_goal") if field == "steps" else None,
                "trend": _build_trend(rows, field, higher_is_better=higher_is_better, tolerance=tolerance),
            }
        )
    return cards


def _recovery_table(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    table = []
    for row in reversed(rows[-10:]):
        if row.get("hrv") is None and row.get("rhr") is None:
            continue
        table.append(
            {
                "date": row.get("date"),
                "hrv": _round(row.get("hrv"), 1),
                "deep_rmssd": _round(row.get("deep_rmssd"), 1),
                "rhr": row.get("rhr"),
            }
        )
    return table


def _recent_tables(
    activity_rows: list[dict[str, Any]],
    sleep_rows: list[dict[str, Any]],
    daily_rows: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    return {
        "sleep": [
            {
                "date": row.get("date"),
                "score": _round(row.get("sleep_score"), 1),
                "hours": _round((row.get("minutes_asleep") or 0) / 60.0, 1) if row.get("minutes_asleep") is not None else None,
                "deep": row.get("minutes_deep"),
                "rem": row.get("minutes_rem"),
                "light": row.get("minutes_light"),
                "awake": row.get("minutes_awake"),
            }
            for row in reversed(sleep_rows[-10:])
        ],
        "activity": [
            {
                "date": row.get("date"),
                "steps": row.get("steps"),
                "active_minutes": row.get("active_minutes"),
                "active_zone_minutes": row.get("active_zone_minutes"),
                "calories_out": row.get("calories_out"),
                "exercise_examples": row.get("exercise_examples"),
            }
            for row in reversed(activity_rows[-10:])
        ],
        "recovery": _recovery_table(daily_rows),
    }


def _source_files(profile_id: str | None) -> dict[str, str]:
    files = {
        "dashboard_cache": cache_path_for(profile_id, DASHBOARD_FILENAME),
        "profile_snapshot": cache_path_for(profile_id, SNAPSHOT_FILENAME),
        "tokens": tokens_file_for(profile_id),
        "client": client_credentials_file_for(profile_id),
    }
    for key, filename in DATASET_FILES.items():
        files[f"{key}_csv"] = csv_path_for(profile_id, filename)
    return files


def _latest_source_mtime(profile_id: str | None) -> float:
    mtimes = []
    for path in _source_files(profile_id).values():
        if os.path.exists(path):
            try:
                mtimes.append(os.path.getmtime(path))
            except Exception:
                continue
    return max(mtimes) if mtimes else 0.0


def _needs_rebuild(profile_id: str | None, dashboard_path: str) -> bool:
    if not os.path.exists(dashboard_path):
        return True
    try:
        cache_mtime = os.path.getmtime(dashboard_path)
    except Exception:
        return True
    return _latest_source_mtime(profile_id) > cache_mtime


def build_dashboard_cache(profile_id: str | None) -> dict[str, Any]:
    activity_rows = _parse_activity_rows(_read_csv_rows(csv_path_for(profile_id, DATASET_FILES["activity"])))
    sleep_rows, nap_counts = _select_sleep_rows(_read_csv_rows(csv_path_for(profile_id, DATASET_FILES["sleep"])))
    hrv_rows = _parse_hrv_rows(_read_csv_rows(csv_path_for(profile_id, DATASET_FILES["hrv"])))
    rhr_rows = _parse_rhr_rows(_read_csv_rows(csv_path_for(profile_id, DATASET_FILES["rhr"])))
    snapshot = _read_json(cache_path_for(profile_id, SNAPSHOT_FILENAME))

    daily_rows = _merge_daily_rows(activity_rows, sleep_rows, hrv_rows, rhr_rows, nap_counts)
    weekly_rows = _aggregate_rows(daily_rows, period="week")[-16:]
    monthly_rows = _aggregate_rows(daily_rows, period="month")[-12:]

    profile = _profile_from_snapshot(profile_id, snapshot)
    recovery = _recovery_score(daily_rows, profile.get("sleep_goal_minutes"))
    metric_cards = _metric_cards(daily_rows, profile)
    activity_section, activity_log_rows = _build_activity_tables(snapshot)
    body_section, body_rows = _build_body_tables(profile, snapshot)
    vitals_section, vitals_rows = _build_vitals_tables(snapshot)
    lifestyle_section, food_rows = _build_lifestyle_tables(snapshot)
    account_section, device_rows, badge_rows, alarm_rows, endpoint_rows = _build_account_tables(profile, snapshot)

    latest_row = daily_rows[-1] if daily_rows else {}
    fetch_summary = snapshot.get("fetch_summary") or {}
    missing_scopes = [scope for scope in profile.get("requested_scopes", []) if scope not in (profile.get("scopes") or [])]

    coverage = {
        "activity": _dataset_meta(activity_rows),
        "sleep": _dataset_meta(sleep_rows),
        "hrv": _dataset_meta(hrv_rows),
        "rhr": _dataset_meta(rhr_rows),
        "daily": _dataset_meta(daily_rows),
        "snapshot": {
            "count": fetch_summary.get("ok", 0),
            "start_date": (snapshot.get("range") or {}).get("recent_start"),
            "end_date": snapshot.get("saved_at"),
        },
    }

    correlations = [
        _build_correlation(daily_rows, "sleep_score", "hrv", "睡眠得分 vs HRV"),
        _build_correlation(daily_rows, "steps", "sleep_score", "步数 vs 睡眠得分"),
        _build_correlation(daily_rows, "sleep_score", "rhr", "睡眠得分 vs 静息心率"),
    ]

    payload = {
        "generated_at": _now_iso(),
        "profile": profile,
        "overview": {
            "recovery_score": recovery.get("score"),
            "recovery_label": recovery.get("label"),
            "latest_date": latest_row.get("date"),
            "latest_sync_at": profile.get("last_snapshot_at"),
            "tracked_days": len(daily_rows),
            "sleep_goal_minutes": profile.get("sleep_goal_minutes"),
            "daily_steps_goal": profile.get("daily_steps_goal"),
            "device_count": profile.get("device_count"),
            "badge_count": profile.get("badge_count"),
            "missing_scopes_count": len(missing_scopes),
            "snapshot_ok_count": fetch_summary.get("ok"),
            "snapshot_total_count": fetch_summary.get("total"),
            "nap_count_today": latest_row.get("nap_count"),
        },
        "coverage": coverage,
        "stats": metric_cards,
        "correlations": correlations,
        "charts": {
            "daily": daily_rows[-90:],
            "weekly": weekly_rows,
            "monthly": monthly_rows,
        },
        "sections": {
            "activity": activity_section,
            "body": body_section,
            "vitals": vitals_section,
            "lifestyle": lifestyle_section,
            "account": account_section,
        },
        "tables": {
            **_recent_tables(activity_rows, sleep_rows, daily_rows),
            "activity_logs": activity_log_rows,
            "body": body_rows,
            "vitals": vitals_rows,
            "foods": food_rows,
            "devices": device_rows,
            "badges": badge_rows,
            "alarms": alarm_rows,
            "endpoints": endpoint_rows,
        },
        "snapshot_status": {
            "has_snapshot": bool(snapshot),
            "saved_at": snapshot.get("saved_at"),
            "has_profile": bool(_endpoint_entry(snapshot, "profile").get("ok")),
            "has_sleep_goal": bool(_endpoint_entry(snapshot, "sleep_goal").get("ok")),
            "scopes": profile.get("scopes") or [],
            "requested_scopes": profile.get("requested_scopes") or [],
            "missing_scopes": missing_scopes,
            "fetch_summary": fetch_summary,
        },
        "files": _source_files(profile_id),
    }

    dashboard_path = cache_path_for(profile_id, DASHBOARD_FILENAME)
    _write_json(dashboard_path, payload)
    return payload


def load_dashboard_cache(profile_id: str | None, rebuild_if_missing: bool = True) -> dict[str, Any]:
    dashboard_path = cache_path_for(profile_id, DASHBOARD_FILENAME)
    if rebuild_if_missing and _needs_rebuild(profile_id, dashboard_path):
        return build_dashboard_cache(profile_id)
    payload = _read_json(dashboard_path)
    if payload:
        return payload
    if rebuild_if_missing:
        return build_dashboard_cache(profile_id)
    return {}


def load_profile_snapshot(profile_id: str | None) -> dict[str, Any]:
    return _read_json(cache_path_for(profile_id, SNAPSHOT_FILENAME))


def load_dataset_rows(profile_id: str | None, dataset: str) -> list[dict[str, Any]]:
    key = str(dataset or "").strip().lower()

    if key == "activity":
        return _parse_activity_rows(_read_csv_rows(csv_path_for(profile_id, DATASET_FILES["activity"])))

    if key == "sleep":
        rows, nap_counts = _select_sleep_rows(_read_csv_rows(csv_path_for(profile_id, DATASET_FILES["sleep"])))
        enriched: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["nap_count"] = nap_counts.get(row.get("date") or "", 0)
            minutes_asleep = item.get("minutes_asleep")
            if minutes_asleep is not None:
                item["sleep_hours"] = _round(minutes_asleep / 60.0, 1)
            deep = item.get("minutes_deep")
            rem = item.get("minutes_rem")
            total = item.get("minutes_asleep")
            if total:
                if deep is not None:
                    item["deep_pct"] = _round((deep / total) * 100.0, 1)
                if rem is not None:
                    item["rem_pct"] = _round((rem / total) * 100.0, 1)
            enriched.append(item)
        return enriched

    if key == "hrv":
        return _parse_hrv_rows(_read_csv_rows(csv_path_for(profile_id, DATASET_FILES["hrv"])))

    if key == "rhr":
        return _parse_rhr_rows(_read_csv_rows(csv_path_for(profile_id, DATASET_FILES["rhr"])))

    if key in {"daily", "weekly", "monthly"}:
        activity_rows = load_dataset_rows(profile_id, "activity")
        sleep_rows = load_dataset_rows(profile_id, "sleep")
        hrv_rows = load_dataset_rows(profile_id, "hrv")
        rhr_rows = load_dataset_rows(profile_id, "rhr")
        nap_counts = {
            str(row.get("date")): int(row.get("nap_count") or 0)
            for row in sleep_rows
            if row.get("date")
        }
        daily_rows = _merge_daily_rows(activity_rows, sleep_rows, hrv_rows, rhr_rows, nap_counts)
        if key == "daily":
            return daily_rows
        if key == "weekly":
            return _aggregate_rows(daily_rows, period="week")
        return _aggregate_rows(daily_rows, period="month")

    return []


def build_profile_cards(profile_ids: list[str] | None = None) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    for profile_id in profile_ids or list_profiles():
        dashboard = load_dashboard_cache(profile_id, rebuild_if_missing=True)
        profile = dashboard.get("profile") or {}
        overview = dashboard.get("overview") or {}
        stats = {item.get("key"): item for item in dashboard.get("stats") or []}
        cards.append(
            {
                "id": profile_id,
                "display_name": profile.get("display_name") or profile_id,
                "member_since": profile.get("member_since"),
                "last_sync_at": overview.get("latest_sync_at"),
                "recovery_score": overview.get("recovery_score"),
                "recovery_label": overview.get("recovery_label"),
                "sleep_score": (stats.get("sleep_score") or {}).get("latest"),
                "steps": (stats.get("steps") or {}).get("latest"),
                "hrv": (stats.get("hrv") or {}).get("latest"),
                "rhr": (stats.get("rhr") or {}).get("latest"),
                "latest_date": overview.get("latest_date"),
            }
        )
    cards.sort(key=lambda item: str(item.get("display_name") or item.get("id")))
    return cards
