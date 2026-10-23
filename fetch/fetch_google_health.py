"""Incrementally refresh a Google-authorized profile without losing Fitbit history."""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.google_health import (
    ACTIVITY_COLUMNS, SLEEP_COLUMNS, ACTIVITY_SCOPE, METRICS_SCOPE, SLEEP_SCOPE,
    PROFILE_SCOPE, SETTINGS_SCOPE, GoogleHealthClient, GoogleHealthError,
    activity_row, civil_datetime, date_value, date_windows, duration_ms, hrv_row,
    number, rhr_row, sleep_row, profile_fetch_lock,
)
from common.profile_paths import cache_path_for, csv_path_for, get_active_profile
from common.google_health_extras import fetch_extras

FALLBACK_START = date(2018, 8, 1)
OVERLAP_DAYS = 7
SNAPSHOT_FILE = "fitbit_profile_snapshot.json"
CURSOR_FILE = "google_health_sync.json"
CORE_SPECS = [
    # type, CSV, columns, required scope, mapping, first existing column
    ("steps", "fitbit_activity.csv", ACTIVITY_COLUMNS, ACTIVITY_SCOPE, None, "steps"),
    ("distance", "fitbit_activity.csv", ACTIVITY_COLUMNS, ACTIVITY_SCOPE, None, "distance"),
    ("floors", "fitbit_activity.csv", ACTIVITY_COLUMNS, ACTIVITY_SCOPE, None, "floors"),
    ("total-calories", "fitbit_activity.csv", ACTIVITY_COLUMNS, ACTIVITY_SCOPE, None, "caloriesOut"),
    ("active-minutes", "fitbit_activity.csv", ACTIVITY_COLUMNS, ACTIVITY_SCOPE, None, "activeMinutes"),
    ("active-zone-minutes", "fitbit_activity.csv", ACTIVITY_COLUMNS, ACTIVITY_SCOPE, None, "activeZoneMinutes"),
    ("daily-resting-heart-rate", "fitbit_rhr.csv", ["date", "resting_heart_rate"], METRICS_SCOPE, "daily_resting_heart_rate.date", "resting_heart_rate"),
    ("daily-heart-rate-variability", "fitbit_hrv.csv", ["date", "dailyRmssd", "deepRmssd"], METRICS_SCOPE, "daily_heart_rate_variability.date", "dailyRmssd"),
    ("sleep", "fitbit_sleep.csv", SLEEP_COLUMNS, SLEEP_SCOPE, "sleep.interval.civil_end_time", "minutesAsleep"),
]
UNSUPPORTED = {
    "badges": ("徽章", "account"), "device_alarms": ("设备闹钟", "account"),
    "sleep_goal": ("睡眠目标", "sleep"), "activity_goals_daily": ("每日活动目标", "activity"),
    "activity_goals_weekly": ("每周活动目标", "activity"), "lifetime_stats": ("终身活动统计", "activity"),
    "recent_activity_types": ("近期活动类型", "activity"), "frequent_activity_types": ("常做活动类型", "activity"),
    "favorite_activity_types": ("收藏活动类型", "activity"), "weight_goal": ("体重目标", "body"),
    "fat_goal": ("体脂目标", "body"), "food_goal": ("营养目标", "nutrition"),
    "water_goal": ("饮水目标", "nutrition"), "recent_foods": ("近期食物", "nutrition"),
    "frequent_foods": ("常吃食物", "nutrition"), "favorite_foods": ("收藏食物", "nutrition"), "meals": ("餐食模板", "nutrition"),
}


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_json(path):
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as handle:
        result = json.load(handle)
    if not isinstance(result, dict):
        raise ValueError("Expected an object in " + os.path.basename(path))
    return result


def atomic_json(path, payload):
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", dir=directory, encoding="utf-8", delete=False) as handle:
            temporary = handle.name
            json.dump(payload, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def csv_rows(path):
    if not os.path.exists(path):
        return [], []
    with open(path, encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or "date" not in reader.fieldnames:
            raise ValueError("Invalid existing CSV: " + os.path.basename(path))
        return list(reader), reader.fieldnames


def merge_csv(path, incoming, columns, *, sleep=False):
    """Only write returned values; keep history and unknown/unsupported fields."""
    if not incoming:
        return
    existing, old_columns = csv_rows(path)
    columns = list(dict.fromkeys(list(old_columns) + list(columns) + [key for row in incoming for key in row]))
    if sleep:
        # Reconcile returns all sessions for a queried civil day. Replacing just
        # those returned days removes cross-provider IDs for the same night.
        returned_dates = {row["date"] for row in incoming}
        old_sessions = existing
        existing = [row for row in old_sessions if row.get("date") not in returned_dates]
        unique = {}
        for row in incoming:
            def same_time(left, right):
                if not left or not right:
                    return False
                try:
                    return datetime.fromisoformat(left).replace(microsecond=0) == datetime.fromisoformat(right).replace(microsecond=0)
                except ValueError:
                    return False
            matches = [old for old in old_sessions if old.get("date") == row["date"] and (
                (row.get("logId") and old.get("logId") == row["logId"]) or
                (same_time(old.get("startTime"), row.get("startTime")) and same_time(old.get("endTime"), row.get("endTime"))))]
            if not matches and str(row.get("isMainSleep")).lower() == "true":
                matches = [old for old in old_sessions if old.get("date") == row["date"] and str(old.get("isMainSleep")).lower() == "true"]
            if len(matches) == 1:
                row = {**matches[0], **{key: value for key, value in row.items() if value is not None}}
            key = row.get("logId") or (row["date"], row.get("startTime"), row.get("endTime"))
            unique[key] = row
        merged = existing + list(unique.values())
    else:
        by_date = {row["date"]: row for row in existing if row.get("date")}
        for row in incoming:
            target = by_date.setdefault(row["date"], {"date": row["date"]})
            target.update({key: value for key, value in row.items() if value is not None})
        merged = list(by_date.values())
    merged.sort(key=lambda row: (row.get("date", ""), str(row.get("startTime", ""))))
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", dir=directory, encoding="utf-8", newline="", delete=False) as handle:
            temporary = handle.name
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader()
            writer.writerows(merged)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def start_date_for(path, metric, column, cursors, fallback):
    checkpoint = cursors.get(metric)
    if checkpoint:
        return max(fallback, date.fromisoformat(checkpoint) - timedelta(days=OVERLAP_DAYS - 1))
    rows, _ = csv_rows(path)
    values = [row["date"] for row in rows if row.get("date") and number(row.get(column)) is not None]
    if values:
        return max(fallback, date.fromisoformat(max(values)) - timedelta(days=OVERLAP_DAYS - 1))
    return fallback


def endpoint_result(*, scope="", group="account", label="", data=None, error=None, skipped=None):
    result = {"ok": error is None and skipped is None, "status": 200 if error is None and skipped is None else getattr(error, "status", None),
              "scope": scope, "group": group, "label": label, "fetched_at": now_iso(), "provider": "google_health"}
    if data is not None:
        result["data"] = data
    if error is not None:
        result["error"] = str(error)
    if skipped:
        result.update(skipped=True, reason=skipped)
    return result


def preserve_cached(previous, result):
    if not result.get("ok") and isinstance(previous, dict) and "data" in previous:
        result.update(data=previous["data"], stale=True,
                      cached_at=previous.get("cached_at") or previous.get("fetched_at"),
                      cached_provider=previous.get("cached_provider") or previous.get("provider") or "fitbit")
    return result


def exercise_rows(points):
    rows = []
    for point in points:
        payload = point.get("exercise") or {}
        interval, metrics = payload.get("interval") or {}, payload.get("metricsSummary") or {}
        distance = number(metrics.get("distanceMillimeters"))
        active_duration = str(payload.get("activeDuration") or "")
        seconds = number(active_duration[:-1]) if active_duration.endswith("s") else None
        rows.append({"logId": point.get("dataPointName") or point.get("name"),
                     "activityName": payload.get("displayName") or payload.get("exerciseType"),
                     "startTime": civil_datetime(interval.get("civilStartTime")),
                     "duration": seconds * 1000 if seconds is not None else duration_ms(interval),
                     "distance": distance / 1_000_000 if distance is not None else None,
                     "calories": number(metrics.get("caloriesKcal")), "steps": number(metrics.get("steps")),
                     "logType": "Google Health"})
    return rows


def sync_profile(profile_id, *, client=None, today=None):
    client = client or GoogleHealthClient(profile_id)
    snapshot_path = cache_path_for(profile_id, SNAPSHOT_FILE)
    old_snapshot = read_json(snapshot_path)
    previous = old_snapshot.get("endpoints") or {}
    endpoints = {}

    def fetch_extra(key, scope, label, group, call):
        if scope not in client.scopes:
            result = endpoint_result(scope=scope, label=label, group=group, skipped="missing_scope:" + scope)
        else:
            try:
                result = endpoint_result(scope=scope, label=label, group=group, data=call())
            except (GoogleHealthError, PermissionError, ValueError) as exc:
                result = endpoint_result(scope=scope, label=label, group=group, error=exc)
        endpoints[key] = preserve_cached(previous.get(key), result)
        return result.get("data") if result.get("ok") else None

    profile = fetch_extra("profile", PROFILE_SCOPE, "个人资料", "account", client.profile)
    legacy_user = dict(((previous.get("profile") or {}).get("data") or {}).get("user") or {})
    if profile is not None:
        member_since = date_value(profile.get("membershipStartDate"))
        if member_since:
            legacy_user["memberSince"] = member_since
        endpoints["profile"]["data"] = {"user": legacy_user, "googleHealthProfile": profile}
        endpoints["profile"]["legacy_metadata_cached"] = bool(legacy_user)
    settings = fetch_extra("settings", SETTINGS_SCOPE, "账户设置", "account", client.settings)
    if settings is not None:
        for source, target in (("timeZone", "timezone"), ("languageLocale", "locale")):
            if settings.get(source):
                legacy_user[target] = settings[source]
        # The compatibility user object is also used for the profile's calendar.
        if "data" in endpoints["profile"]:
            endpoints["profile"]["data"]["user"] = legacy_user
    if today is None:
        try:
            today = datetime.now(ZoneInfo(legacy_user.get("timezone") or os.environ.get("TZ") or "UTC")).date()
        except (ZoneInfoNotFoundError, ValueError):
            today = datetime.now(timezone.utc).date()
    end = today + timedelta(days=1)
    try:
        fallback = date.fromisoformat(legacy_user.get("memberSince") or FALLBACK_START.isoformat())
    except ValueError:
        fallback = FALLBACK_START
    recent_start = max(fallback, today - timedelta(days=29))
    state_path = cache_path_for(profile_id, CURSOR_FILE)
    state = read_json(state_path)
    cursors = state.setdefault("completed_through", {})
    state["provider"] = "google_health"
    goal = (((previous.get("sleep_goal") or {}).get("data") or {}).get("goal") or {}).get("minDuration")
    sleep_goal = number(goal) or 450
    current_activity = {}

    for metric, filename, columns, scope, filter_field, column in CORE_SPECS:
        key = "google_health_" + metric.replace("-", "_")
        group = "sleep" if metric == "sleep" else "vitals" if scope == METRICS_SCOPE else "activity"
        if scope not in client.scopes:
            endpoints[key] = endpoint_result(scope=scope, group=group, label=metric, skipped="missing_scope:" + scope)
            continue
        path = csv_path_for(profile_id, filename)
        count = 0
        try:
            start = start_date_for(path, metric, column, cursors, fallback)
            print(f"Starting {metric} fetch from {start.isoformat()} (Google Health v4)", flush=True)
            window = 14 if metric in {"active-minutes", "total-calories"} else 90
            for first, stop in date_windows(start, end, window):
                points = list(client.reconcile(metric, first, stop, filter_field)) if filter_field else list(client.daily_rollup(metric, first, stop))
                if metric == "sleep":
                    rows = [sleep_row(point, sleep_goal) for point in points]
                elif metric == "daily-resting-heart-rate":
                    rows = [rhr_row(point) for point in points]
                elif metric == "daily-heart-rate-variability":
                    rows = [hrv_row(point) for point in points]
                else:
                    rows = [activity_row(point, metric) for point in points]
                # Do not advance a cursor past malformed records. An empty
                # successful response is valid, but a missing record date is not.
                if any(row is None for row in rows):
                    raise GoogleHealthError(f"Google Health returned {metric} data without a valid civil date")
                rows = [row for row in rows if first.isoformat() <= row["date"] < stop.isoformat()]
                merge_csv(path, rows, columns, sleep=metric == "sleep")
                if filename == "fitbit_activity.csv":
                    for row in rows:
                        if row["date"] == today.isoformat():
                            current_activity.update({key: value for key, value in row.items() if key != "date"})
                count += len(rows)
                cursors[metric] = (stop - timedelta(days=1)).isoformat()
                state["updated_at"] = now_iso()
                atomic_json(state_path, state)
            endpoints[key] = endpoint_result(scope=scope, group=group, label=metric, data={"records_refreshed": count, "completed_through": cursors.get(metric)})
        except (GoogleHealthError, PermissionError, ValueError) as exc:
            endpoints[key] = endpoint_result(scope=scope, group=group, label=metric, error=exc)
            print(f"Incomplete {metric}: {exc}", flush=True)

    devices = fetch_extra("devices", SETTINGS_SCOPE, "设备", "account", client.devices)
    if devices is not None:
        endpoints["devices"]["data"] = [{"id": device.get("name"), "type": device.get("deviceType"),
            "deviceVersion": device.get("deviceVersion"), "battery": device.get("batteryStatus"),
            "batteryLevel": device.get("batteryLevel"), "lastSyncTime": device.get("lastSyncTime"),
            "mac": device.get("macAddress")} for device in devices]
    fetch_extra("activity_log_list", ACTIVITY_SCOPE, "活动日志列表", "activity", lambda: {
        "activities": exercise_rows(client.reconcile("exercise", recent_start, end, "exercise.interval.civil_start_time"))})
    for key, result in fetch_extras(client, recent_start, end).items():
        endpoints[key] = preserve_cached(previous.get(key), result)

    rollups_ok = all(endpoints["google_health_" + metric.replace("-", "_")].get("ok") for metric, *_ in CORE_SPECS[:6])
    if current_activity and rollups_ok:
        google_fields = {"steps", "distance", "floors", "caloriesOut", "lightlyActiveMinutes", "fairlyActiveMinutes", "veryActiveMinutes", "activeMinutes", "activeZoneMinutes"}
        summary = {key: number(current_activity.get(key)) for key in google_fields}
        if summary.get("distance") is not None:
            summary["distances"] = [{"activity": "total", "distance": summary["distance"]}]
        endpoints["today_activity_summary"] = endpoint_result(scope=ACTIVITY_SCOPE, group="activity", label="今日活动摘要", data={"summary": summary})
    else:
        endpoints["today_activity_summary"] = endpoint_result(scope=ACTIVITY_SCOPE, group="activity", label="今日活动摘要", skipped="no_complete_current_day_data")

    # Unsupported legacy extras remain reviewable as historical cache, with an
    # explicit stale marker. Never present them as newly downloaded Google data.
    for key in set(previous) | set(UNSUPPORTED):
        if key in endpoints:
            continue
        old = previous.get(key) or {}
        label, group = UNSUPPORTED.get(key, (old.get("label", key), old.get("group", "other")))
        endpoints[key] = preserve_cached(old, endpoint_result(label=label, group=group, skipped="unavailable_in_google_health_v4"))

    supported = [entry for entry in endpoints.values() if entry.get("reason") not in {"unavailable_in_google_health_v4", "no_complete_current_day_data"}]
    complete = all(entry.get("ok") for entry in supported)
    from common.google_health_auth import GOOGLE_HEALTH_SCOPES
    snapshot = {"profile_id": profile_id, "provider": "google_health", "api_version": "v4", "saved_at": now_iso(),
        "sync_status": "complete" if complete else "partial", "requested_scopes": list(GOOGLE_HEALTH_SCOPES),
        "token_scope": sorted(client.scopes), "token_scope_raw": " ".join(sorted(client.scopes)),
        "token_user_id": client.tokens.get("health_user_id"), "token_type": client.tokens.get("token_type"),
        "expires_in": client.tokens.get("expires_in"), "range": {"today": today.isoformat(), "recent_start": recent_start.isoformat(), "recent_days": 30},
        "fetch_summary": {"total": len(endpoints), "ok": sum(bool(entry.get("ok")) for entry in endpoints.values()),
            "skipped": sum(bool(entry.get("skipped")) for entry in endpoints.values()),
            "failed": sum(not entry.get("ok") and not entry.get("skipped") for entry in endpoints.values())},
        "endpoints": endpoints}
    atomic_json(snapshot_path, snapshot)
    print(f"Google Health sync {snapshot['sync_status']}: {snapshot['fetch_summary']}", flush=True)
    return 0 if complete else 2


def main():
    parser = argparse.ArgumentParser(description="Fetch Google Health v4 data for an authorized profile")
    parser.add_argument("--profile", default=None)
    args = parser.parse_args()
    try:
        profile_id = get_active_profile(args.profile)
        with profile_fetch_lock(profile_id):
            return sync_profile(profile_id)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Google Health sync failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
