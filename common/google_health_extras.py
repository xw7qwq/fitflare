"""Adapt supported Google Health measurements to the existing dashboard snapshot.

Schemas: https://developers.google.com/health/reference/rest/v4/users.dataTypes.dataPoints
Goals, badges, alarms, and food favorites have no equivalent in the v4 surface.
"""
from datetime import datetime, timedelta, timezone

from common.google_health import (
    GoogleHealthError, METRICS_SCOPE, SCOPE_PREFIX, civil_date, civil_datetime, date_value, number,
)

NUTRITION_SCOPE = SCOPE_PREFIX + "nutrition.readonly"


def _sample_date(sample):
    civil = civil_date(sample.get("civilTime"))
    if civil:
        return civil
    physical = sample.get("physicalTime")
    if not physical:
        return None
    try:
        stamp = datetime.fromisoformat(physical.replace("Z", "+00:00"))
        offset = float(str(sample.get("utcOffset", "0s")).removesuffix("s"))
        return (stamp.astimezone(timezone.utc) + timedelta(seconds=offset)).date().isoformat()
    except (TypeError, ValueError):
        return None


def _session_date(interval):
    return civil_date(interval.get("civilStartTime")) or _sample_date({
        "physicalTime": interval.get("startTime"), "utcOffset": interval.get("startUtcOffset", "0s"),
    })


def _body(points, kind):
    rows = []
    for point in points:
        value = point.get("weight" if kind == "weight" else "bodyFat") or {}
        day = _sample_date(value.get("sampleTime") or {})
        amount = number(value.get("weightGrams" if kind == "weight" else "percentage"))
        if day and amount is not None:
            rows.append({"date": day, kind: amount / 1000 if kind == "weight" else amount,
                         "observationTime": (value.get("sampleTime") or {}).get("physicalTime")
                         or civil_datetime((value.get("sampleTime") or {}).get("civilTime")) or day,
                         "source": "Google Health"})
    return {kind: sorted(rows, key=lambda row: (row["date"], row["observationTime"]))}


def _vitals(points, kind):
    rows = []
    field = {"br": "dailyRespiratoryRate", "spo2": "dailyOxygenSaturation",
             "tempSkin": "dailySleepTemperatureDerivations"}[kind]
    for point in points:
        value = point.get(field) or {}
        day = date_value(value.get("date"))
        if not day:
            continue
        if kind == "br":
            measurements = {"breathingRate": number(value.get("breathsPerMinute"))}
        elif kind == "spo2":
            # Confidence bounds are not the minimum/maximum observed SpO2.
            if number(value.get("averagePercentage")) is None:
                continue
            measurements = {"avg": number(value.get("averagePercentage")),
                            "lowerBoundPercentage": number(value.get("lowerBoundPercentage")),
                            "upperBoundPercentage": number(value.get("upperBoundPercentage"))}
        else:
            nightly, baseline = number(value.get("nightlyTemperatureCelsius")), number(value.get("baselineTemperatureCelsius"))
            measurements = {"nightlyRelative": nightly - baseline if nightly is not None and baseline is not None else None}
            # Preserve the relative-temperature meaning of the existing chart.
            # A missing baseline must not turn an absolute temperature into a delta.
        measurements = {key: amount for key, amount in measurements.items() if amount is not None}
        if measurements:
            rows.append({"dateTime": day, "value": measurements})
    return {kind: sorted(rows, key=lambda row: row["dateTime"])}


def _foods(points):
    rows = []
    for point in points:
        value = point.get("nutritionLog") or {}
        day = _session_date(value.get("interval") or {})
        if not day:
            continue
        row = {"date": day, "name": value.get("foodDisplayName"),
               "calories": number((value.get("energy") or {}).get("kcal"))}
        rows.append(row)
    return sorted(rows, key=lambda row: row["date"], reverse=True)


def _water(points):
    rows = []
    for point in points:
        value = point.get("hydrationLog") or {}
        day = _session_date(value.get("interval") or {})
        amount = number((value.get("amountConsumed") or {}).get("milliliters"))
        if day:
            rows.append({"date": day, "amount": amount})
    return rows


def fetch_extras(client, start, end):
    """Return supported snapshot entries for [start, end), with end-1 as today.

    A denied permission or failed endpoint remains explicitly unavailable. Other
    granted measurements continue syncing, and an absent optional value stays absent.
    """
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    today = end - timedelta(days=1)
    entries = {}

    def fetch(key, label, group, scope, data_type, filter_field, adapter, first=start, stop=end):
        metadata = {"label": label, "group": group, "scope": scope, "fetched_at": fetched_at,
                    "supported": True, "provider": "google_health"}
        if scope not in client.scopes:
            entries[key] = {**metadata, "ok": False, "status": None, "skipped": True,
                            "reason": "missing_scope:" + scope}
            return None
        try:
            points = list(client.reconcile(data_type, first, stop, filter_field))
            data = adapter(points)
        except (GoogleHealthError, PermissionError, ValueError) as exc:
            entries[key] = {**metadata, "ok": False, "status": getattr(exc, "status", None), "error": str(exc)}
            return None
        entries[key] = {**metadata, "ok": True, "status": 200, "data": data}
        return data

    fetch("weight_log_recent", "近期体重记录", "body", METRICS_SCOPE, "weight",
          "weight.sample_time.civil_time", lambda points: _body(points, "weight"))
    fetch("fat_log_recent", "近期体脂记录", "body", METRICS_SCOPE, "body-fat",
          "body_fat.sample_time.civil_time", lambda points: _body(points, "fat"))

    for key, label, data_type, kind in (
        ("breathing_rate", "呼吸率", "daily-respiratory-rate", "br"),
        ("spo2", "血氧", "daily-oxygen-saturation", "spo2"),
        ("skin_temperature", "皮温变化", "daily-sleep-temperature-derivations", "tempSkin"),
    ):
        recent = key + "_recent"
        payload = fetch(recent, "近期" + label, "vitals", METRICS_SCOPE, data_type,
                        data_type.replace("-", "_") + ".date", lambda points, kind=kind: _vitals(points, kind))
        entries[key + "_today"] = {**entries[recent], "label": "今日" + label}
        if payload is not None:
            entries[key + "_today"]["data"] = {kind: [row for row in payload[kind] if row["dateTime"] == today.isoformat()]}

    food_rows = fetch("recent_foods", "近期饮食记录", "nutrition", NUTRITION_SCOPE,
                      "nutrition-log", "nutrition_log.interval.civil_start_time", _foods)
    entries["food_log_today"] = {**entries["recent_foods"], "label": "今日饮食日志"}
    if food_rows is not None:
        today_rows = [row for row in food_rows if row["date"] == today.isoformat()]
        known_calories = [row["calories"] for row in today_rows if row["calories"] is not None]
        # No observations and partially known energy cannot establish a total.
        total = sum(known_calories) if today_rows and len(known_calories) == len(today_rows) else None
        entries["food_log_today"]["data"] = {"foods": today_rows, "summary": {"calories": total}}
        entries["recent_foods"]["data"] = {"foods": [{**row, "dateLastEaten": row["date"]} for row in food_rows]}

    water_rows = fetch("water_log_today", "今日饮水日志", "nutrition", NUTRITION_SCOPE,
                       "hydration-log", "hydration_log.interval.civil_start_time", _water, first=today)
    if water_rows is not None:
        amounts = [row["amount"] for row in water_rows if row["amount"] is not None]
        total = sum(amounts) if water_rows and len(amounts) == len(water_rows) else None
        entries["water_log_today"]["data"] = {"water": water_rows, "summary": {"water": total}}
    return entries
