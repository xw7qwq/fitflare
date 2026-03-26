import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta
from typing import Any

import requests

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from auth.refresh_token import refresh_token
from common.fitbit_scopes import FITBIT_DASHBOARD_SCOPES
from common.profile_paths import (
    cache_path_for,
    ensure_dirs_for_cache,
    get_active_profile,
    tokens_file_for,
)
from common.rate_limit import wait_seconds_with_countdown, wait_until_next_hour_with_countdown


RECENT_INTERVAL_DAYS = 30
ACTIVITY_LOG_LIMIT = 20

parser = argparse.ArgumentParser(add_help=False)
parser.add_argument("--profile", default=None)
args, _unknown = parser.parse_known_args()
PROFILE_ID = get_active_profile(args.profile)

TOKENS_FILE = tokens_file_for(PROFILE_ID)
SNAPSHOT_FILE = cache_path_for(PROFILE_ID, "fitbit_profile_snapshot.json")
os.environ["FITBIT_TOKENS_FILE"] = TOKENS_FILE
os.environ["FITBIT_PROFILE"] = PROFILE_ID or ""
ensure_dirs_for_cache(SNAPSHOT_FILE)


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _load_tokens() -> dict[str, Any]:
    with open(TOKENS_FILE, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    return data if isinstance(data, dict) else {}


def _get_token_scopes() -> list[str]:
    scope_text = str(_load_tokens().get("scope", "")).strip()
    return [part for part in scope_text.split() if part]


def _get_access_token() -> str:
    token = str(_load_tokens().get("access_token", "")).strip()
    if not token:
        token = refresh_token()
    if not token:
        raise RuntimeError("Missing access token")
    return token


def _throttle_if_needed(response: requests.Response) -> None:
    try:
        remaining = int(response.headers.get("fitbit-rate-limit-remaining", "1"))
    except Exception:
        remaining = 1
    try:
        reset_seconds = int(response.headers.get("fitbit-rate-limit-reset", "0"))
    except Exception:
        reset_seconds = 0
    if remaining <= 0 and reset_seconds > 0:
        print(f"Rate-limit headers indicate reset in {reset_seconds}s.")
        wait_seconds_with_countdown(reset_seconds + 1, context="Header reset")


def _request_json(url: str, label: str, token: str) -> tuple[dict[str, Any], str]:
    headers = {"Authorization": f"Bearer {token}"}

    for attempt in range(4):
        try:
            response = requests.get(url, headers=headers, timeout=30)
        except requests.RequestException as exc:
            if attempt >= 3:
                return {
                    "ok": False,
                    "status": None,
                    "error": str(exc),
                    "fetched_at": _now_iso(),
                }, token
            time.sleep(1 + attempt)
            continue

        print(f"Response {response.status_code} for {label}")
        _throttle_if_needed(response)

        if response.status_code == 200:
            try:
                data = response.json()
            except ValueError:
                return {
                    "ok": False,
                    "status": 200,
                    "error": "Response was not valid JSON",
                    "fetched_at": _now_iso(),
                }, token
            return {
                "ok": True,
                "status": 200,
                "fetched_at": _now_iso(),
                "data": data,
            }, token

        if response.status_code == 401:
            print(f"Token expired while fetching {label}. Refreshing...")
            token = refresh_token()
            headers = {"Authorization": f"Bearer {token}"}
            continue

        if response.status_code == 429:
            try:
                reset_seconds = int(response.headers.get("fitbit-rate-limit-reset", "0"))
            except Exception:
                reset_seconds = 0
            if reset_seconds > 0:
                wait_seconds_with_countdown(reset_seconds + 1, context="Header reset")
            else:
                wait_until_next_hour_with_countdown(f"Rate limited by Fitbit ({label})")
            continue

        if response.status_code in (500, 502, 503, 504):
            if attempt >= 3:
                return {
                    "ok": False,
                    "status": response.status_code,
                    "error": response.text,
                    "fetched_at": _now_iso(),
                }, token
            time.sleep(1 + attempt)
            continue

        return {
            "ok": False,
            "status": response.status_code,
            "error": response.text,
            "fetched_at": _now_iso(),
        }, token

    return {
        "ok": False,
        "status": None,
        "error": "Exhausted retries",
        "fetched_at": _now_iso(),
    }, token


def _build_endpoint_specs(today: str, recent_start: str) -> list[dict[str, str]]:
    return [
        {
            "key": "profile",
            "label": "个人资料",
            "group": "account",
            "scope": "profile",
            "url": "https://api.fitbit.com/1/user/-/profile.json",
        },
        {
            "key": "badges",
            "label": "徽章",
            "group": "account",
            "scope": "profile",
            "url": "https://api.fitbit.com/1/user/-/badges.json",
        },
        {
            "key": "devices",
            "label": "设备",
            "group": "account",
            "scope": "settings",
            "url": "https://api.fitbit.com/1/user/-/devices.json",
        },
        {
            "key": "sleep_goal",
            "label": "睡眠目标",
            "group": "sleep",
            "scope": "sleep",
            "url": "https://api.fitbit.com/1.2/user/-/sleep/goal.json",
        },
        {
            "key": "activity_goals_daily",
            "label": "每日活动目标",
            "group": "activity",
            "scope": "activity",
            "url": "https://api.fitbit.com/1/user/-/activities/goals/daily.json",
        },
        {
            "key": "activity_goals_weekly",
            "label": "每周活动目标",
            "group": "activity",
            "scope": "activity",
            "url": "https://api.fitbit.com/1/user/-/activities/goals/weekly.json",
        },
        {
            "key": "today_activity_summary",
            "label": "今日活动摘要",
            "group": "activity",
            "scope": "activity",
            "url": f"https://api.fitbit.com/1/user/-/activities/date/{today}.json",
        },
        {
            "key": "lifetime_stats",
            "label": "终身活动统计",
            "group": "activity",
            "scope": "activity",
            "url": "https://api.fitbit.com/1/user/-/activities.json",
        },
        {
            "key": "activity_log_list",
            "label": "活动日志列表",
            "group": "activity",
            "scope": "activity",
            "url": f"https://api.fitbit.com/1/user/-/activities/list.json?beforeDate={today}&sort=desc&offset=0&limit={ACTIVITY_LOG_LIMIT}",
        },
        {
            "key": "recent_activity_types",
            "label": "近期活动类型",
            "group": "activity",
            "scope": "activity",
            "url": "https://api.fitbit.com/1/user/-/activities/recent.json",
        },
        {
            "key": "frequent_activity_types",
            "label": "常做活动类型",
            "group": "activity",
            "scope": "activity",
            "url": "https://api.fitbit.com/1/user/-/activities/frequent.json",
        },
        {
            "key": "favorite_activity_types",
            "label": "收藏活动类型",
            "group": "activity",
            "scope": "activity",
            "url": "https://api.fitbit.com/1/user/-/activities/favorite.json",
        },
        {
            "key": "weight_goal",
            "label": "体重目标",
            "group": "body",
            "scope": "weight",
            "url": "https://api.fitbit.com/1/user/-/body/log/weight/goal.json",
        },
        {
            "key": "weight_log_recent",
            "label": "近期体重记录",
            "group": "body",
            "scope": "weight",
            "url": f"https://api.fitbit.com/1/user/-/body/log/weight/date/{recent_start}/{today}.json",
        },
        {
            "key": "fat_goal",
            "label": "体脂目标",
            "group": "body",
            "scope": "weight",
            "url": "https://api.fitbit.com/1/user/-/body/log/fat/goal.json",
        },
        {
            "key": "fat_log_recent",
            "label": "近期体脂记录",
            "group": "body",
            "scope": "weight",
            "url": f"https://api.fitbit.com/1/user/-/body/log/fat/date/{recent_start}/{today}.json",
        },
        {
            "key": "food_goal",
            "label": "营养目标",
            "group": "nutrition",
            "scope": "nutrition",
            "url": "https://api.fitbit.com/1/user/-/foods/log/goal.json",
        },
        {
            "key": "food_log_today",
            "label": "今日饮食日志",
            "group": "nutrition",
            "scope": "nutrition",
            "url": f"https://api.fitbit.com/1/user/-/foods/log/date/{today}.json",
        },
        {
            "key": "recent_foods",
            "label": "近期食物",
            "group": "nutrition",
            "scope": "nutrition",
            "url": "https://api.fitbit.com/1/user/-/foods/log/recent.json",
        },
        {
            "key": "frequent_foods",
            "label": "常吃食物",
            "group": "nutrition",
            "scope": "nutrition",
            "url": "https://api.fitbit.com/1/user/-/foods/log/frequent.json",
        },
        {
            "key": "favorite_foods",
            "label": "收藏食物",
            "group": "nutrition",
            "scope": "nutrition",
            "url": "https://api.fitbit.com/1/user/-/foods/log/favorite.json",
        },
        {
            "key": "meals",
            "label": "餐食模板",
            "group": "nutrition",
            "scope": "nutrition",
            "url": "https://api.fitbit.com/1/user/-/meals.json",
        },
        {
            "key": "water_goal",
            "label": "饮水目标",
            "group": "nutrition",
            "scope": "nutrition",
            "url": "https://api.fitbit.com/1/user/-/foods/log/water/goal.json",
        },
        {
            "key": "water_log_today",
            "label": "今日饮水日志",
            "group": "nutrition",
            "scope": "nutrition",
            "url": f"https://api.fitbit.com/1/user/-/foods/log/water/date/{today}.json",
        },
        {
            "key": "breathing_rate_today",
            "label": "今日呼吸率",
            "group": "vitals",
            "scope": "respiratory_rate",
            "url": f"https://api.fitbit.com/1/user/-/br/date/{today}.json",
        },
        {
            "key": "breathing_rate_recent",
            "label": "近期呼吸率",
            "group": "vitals",
            "scope": "respiratory_rate",
            "url": f"https://api.fitbit.com/1/user/-/br/date/{recent_start}/{today}.json",
        },
        {
            "key": "spo2_today",
            "label": "今日血氧",
            "group": "vitals",
            "scope": "oxygen_saturation",
            "url": f"https://api.fitbit.com/1/user/-/spo2/date/{today}.json",
        },
        {
            "key": "spo2_recent",
            "label": "近期血氧",
            "group": "vitals",
            "scope": "oxygen_saturation",
            "url": f"https://api.fitbit.com/1/user/-/spo2/date/{recent_start}/{today}.json",
        },
        {
            "key": "skin_temperature_today",
            "label": "今日皮温",
            "group": "vitals",
            "scope": "temperature",
            "url": f"https://api.fitbit.com/1/user/-/temp/skin/date/{today}.json",
        },
        {
            "key": "skin_temperature_recent",
            "label": "近期皮温",
            "group": "vitals",
            "scope": "temperature",
            "url": f"https://api.fitbit.com/1/user/-/temp/skin/date/{recent_start}/{today}.json",
        },
    ]


def _decorate_result(spec: dict[str, str], result: dict[str, Any]) -> dict[str, Any]:
    payload = dict(result)
    payload["label"] = spec["label"]
    payload["group"] = spec["group"]
    payload["scope"] = spec["scope"]
    return payload


def _skipped_result(spec: dict[str, str], reason: str) -> dict[str, Any]:
    return {
        "ok": False,
        "status": None,
        "skipped": True,
        "reason": reason,
        "fetched_at": _now_iso(),
        "label": spec["label"],
        "group": spec["group"],
        "scope": spec["scope"],
    }


def _fetch_tracker_alarms(
    devices_payload: dict[str, Any] | None,
    token_scopes: set[str],
    token: str,
) -> tuple[dict[str, Any], str]:
    spec = {
        "label": "设备闹钟",
        "group": "account",
        "scope": "settings",
    }
    if "settings" not in token_scopes:
        return {
            "ok": False,
            "status": None,
            "skipped": True,
            "reason": "missing_scope:settings",
            "fetched_at": _now_iso(),
            **spec,
        }, token

    devices = devices_payload.get("data") if isinstance(devices_payload, dict) else None
    trackers = [
        device for device in (devices or [])
        if isinstance(device, dict) and device.get("id") and str(device.get("type", "")).upper() == "TRACKER"
    ]
    if not trackers:
        return {
            "ok": True,
            "status": 200,
            "fetched_at": _now_iso(),
            "data": [],
            **spec,
        }, token

    collected: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for tracker in trackers:
        tracker_id = str(tracker.get("id"))
        tracker_name = tracker.get("deviceVersion") or tracker.get("type") or tracker_id
        url = f"https://api.fitbit.com/1/user/-/devices/tracker/{tracker_id}/alarms.json"
        result, token = _request_json(url, f"tracker alarms {tracker_id}", token)
        if result.get("ok"):
            payload = result.get("data")
            if isinstance(payload, dict):
                alarms = payload.get("trackerAlarms") or payload.get("alarms") or []
            elif isinstance(payload, list):
                alarms = payload
            else:
                alarms = []
            collected.append({
                "device_id": tracker_id,
                "device_name": tracker_name,
                "alarms": alarms,
            })
        else:
            errors.append({
                "device_id": tracker_id,
                "device_name": tracker_name,
                "status": result.get("status"),
                "error": result.get("error"),
            })
        time.sleep(0.15)

    return {
        "ok": not errors,
        "status": 200 if collected or not errors else None,
        "fetched_at": _now_iso(),
        "data": collected,
        "errors": errors,
        **spec,
    }, token


def _build_fetch_summary(endpoints: dict[str, dict[str, Any]]) -> dict[str, Any]:
    entries = list(endpoints.values())
    ok_count = sum(1 for item in entries if item.get("ok"))
    skipped_count = sum(1 for item in entries if item.get("skipped"))
    failed_count = sum(1 for item in entries if not item.get("ok") and not item.get("skipped"))
    groups: dict[str, dict[str, Any]] = {}
    for key, entry in endpoints.items():
        group_key = str(entry.get("group") or "other")
        bucket = groups.setdefault(group_key, {
            "group": group_key,
            "total": 0,
            "ok": 0,
            "skipped": 0,
            "failed": 0,
            "keys": [],
        })
        bucket["total"] += 1
        bucket["keys"].append(key)
        if entry.get("ok"):
            bucket["ok"] += 1
        elif entry.get("skipped"):
            bucket["skipped"] += 1
        else:
            bucket["failed"] += 1
    return {
        "total": len(entries),
        "ok": ok_count,
        "skipped": skipped_count,
        "failed": failed_count,
        "groups": list(groups.values()),
    }


def main() -> None:
    print(f"Starting Fitbit profile snapshot fetch for {PROFILE_ID or 'default'}")

    token = _get_access_token()
    today = datetime.now().strftime("%Y-%m-%d")
    recent_start = (datetime.now() - timedelta(days=RECENT_INTERVAL_DAYS - 1)).strftime("%Y-%m-%d")
    token_scopes = set(_get_token_scopes())

    endpoints: dict[str, dict[str, Any]] = {}
    for spec in _build_endpoint_specs(today, recent_start):
        if spec["scope"] not in token_scopes:
            endpoints[spec["key"]] = _skipped_result(spec, f"missing_scope:{spec['scope']}")
            continue
        result, token = _request_json(spec["url"], spec["key"], token)
        endpoints[spec["key"]] = _decorate_result(spec, result)
        time.sleep(0.2)

    alarms_result, token = _fetch_tracker_alarms(endpoints.get("devices"), token_scopes, token)
    endpoints["device_alarms"] = alarms_result

    tokens = _load_tokens()
    scope_text = str(tokens.get("scope", "")).strip()
    granted_scopes = [part for part in scope_text.split() if part]

    snapshot = {
        "profile_id": PROFILE_ID,
        "saved_at": _now_iso(),
        "requested_scopes": FITBIT_DASHBOARD_SCOPES,
        "token_scope": granted_scopes,
        "token_scope_raw": scope_text,
        "token_user_id": tokens.get("user_id"),
        "token_type": tokens.get("token_type"),
        "expires_in": tokens.get("expires_in"),
        "range": {
            "today": today,
            "recent_start": recent_start,
            "recent_days": RECENT_INTERVAL_DAYS,
            "activity_log_limit": ACTIVITY_LOG_LIMIT,
        },
        "fetch_summary": _build_fetch_summary(endpoints),
        "endpoints": endpoints,
    }

    with open(SNAPSHOT_FILE, "w", encoding="utf-8") as handle:
        json.dump(snapshot, handle, ensure_ascii=False, indent=2)

    summary = snapshot["fetch_summary"]
    print(f"Saved Fitbit profile snapshot to {SNAPSHOT_FILE}")
    print(
        "Fetched snapshot endpoints: "
        f"{summary['ok']} succeeded, {summary['skipped']} skipped, {summary['failed']} failed "
        f"(total {summary['total']})."
    )


if __name__ == "__main__":
    main()
