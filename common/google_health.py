"""Google Health v4 reads and adapters for FitBaus' existing data files.

Wire formats follow https://developers.google.com/health/reference/rest/v4.
Daily totals use the API's reconciled civil-day rollups, never sums of raw
wearable streams (which can contain overlapping measurements).
"""
from __future__ import annotations

import json
import math
import os
import re
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import requests

from common.profile_paths import tokens_file_for

API_BASE = "https://health.googleapis.com/v4"
SCOPE_PREFIX = "https://www.googleapis.com/auth/googlehealth."
ACTIVITY_SCOPE = SCOPE_PREFIX + "activity_and_fitness.readonly"
METRICS_SCOPE = SCOPE_PREFIX + "health_metrics_and_measurements.readonly"
SLEEP_SCOPE = SCOPE_PREFIX + "sleep.readonly"
PROFILE_SCOPE = SCOPE_PREFIX + "profile.readonly"
SETTINGS_SCOPE = SCOPE_PREFIX + "settings.readonly"


class GoogleHealthError(RuntimeError):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def load_tokens(path):
    with open(path, encoding="utf-8") as handle:
        result = json.load(handle)
    if not isinstance(result, dict):
        raise ValueError("Invalid token file")
    return result


def is_google_profile(profile_id=None):
    return load_tokens(tokens_file_for(profile_id)).get("oauth_type") == "google"


def require_legacy_fitbit_tokens(path):
    """Fail before any Fitbit request when a profile has migrated to Google."""
    if load_tokens(path).get("oauth_type") == "google":
        raise RuntimeError("This profile uses Google Health. Run fetch_all.py or fetch_google_health.py.")


@contextmanager
def profile_fetch_lock(profile_id=None):
    """Share the migration/sync lock, including CLI parents and child scripts."""
    path = os.path.abspath(tokens_file_for(profile_id))
    previous = os.environ.get("FITBAUS_FETCH_LOCK_HELD")
    if previous == path:
        yield
        return
    from common.google_health_auth import profile_migration_lock
    with profile_migration_lock(path):
        os.environ["FITBAUS_FETCH_LOCK_HELD"] = path
        try:
            yield
        finally:
            if previous is None:
                os.environ.pop("FITBAUS_FETCH_LOCK_HELD", None)
            else:
                os.environ["FITBAUS_FETCH_LOCK_HELD"] = previous


def granted_scopes(tokens):
    value = tokens.get("granted_scopes", tokens.get("scope", ""))
    return set(value.split() if isinstance(value, str) else value or [])


def date_value(value):
    if not isinstance(value, dict):
        return None
    try:
        return date(int(value["year"]), int(value["month"]), int(value["day"])).isoformat()
    except (KeyError, TypeError, ValueError):
        return None


def civil_date(value):
    return date_value((value or {}).get("date"))


def civil_datetime(value):
    day = civil_date(value)
    if not day:
        return None
    clock = (value or {}).get("time", {})
    return f"{day}T{int(clock.get('hours', 0)):02d}:{int(clock.get('minutes', 0)):02d}:{int(clock.get('seconds', 0)):02d}"


def number(value):
    if value is None or value == "":
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def integer(value):
    result = number(value)
    return int(round(result)) if result is not None else None


def date_windows(start, end, days):
    """Yield closed-open civil date windows."""
    while start < end:
        stop = min(start + timedelta(days=days), end)
        yield start, stop
        start = stop


def _civil(day):
    return {"date": {"year": day.year, "month": day.month, "day": day.day}}


class GoogleHealthClient:
    def __init__(self, profile_id=None, *, tokens_path=None, session=None, sleep=time.sleep, refresh=None):
        self.profile_id = profile_id
        self.tokens_path = tokens_path or tokens_file_for(profile_id)
        self.tokens = load_tokens(self.tokens_path)
        if self.tokens.get("oauth_type") != "google":
            raise GoogleHealthError("Google Health requires a Google-authorized profile")
        self.session = session or requests.Session()
        self.sleep = sleep
        self.refresh = refresh

    @property
    def scopes(self):
        return granted_scopes(self.tokens)

    def _refresh(self):
        from common.google_health_auth import refresh_google_token, GoogleHealthAuthError
        try:
            if self.refresh is None:
                token = refresh_google_token(self.profile_id, tokens_path=self.tokens_path)
            else:
                token = self.refresh()
        except GoogleHealthAuthError as exc:
            raise GoogleHealthError(str(exc), exc.status) from None
        self.tokens = load_tokens(self.tokens_path)
        self.tokens["access_token"] = token

    def request(self, method, path, *, params=None, body=None):
        if not path.startswith("users/me/"):
            raise ValueError("Google Health reads must address users/me")
        refreshed = False
        for attempt in range(4):
            try:
                response = self.session.request(
                    method, API_BASE + "/" + path,
                    headers={"Authorization": "Bearer " + str(self.tokens.get("access_token", ""))},
                    params=params, json=body, timeout=30,
                )
            except requests.RequestException:
                if attempt == 3:
                    raise GoogleHealthError("Google Health request failed after 4 attempts") from None
                self.sleep(2 ** attempt)
                continue
            if response.status_code == 200:
                try:
                    payload = response.json()
                except ValueError:
                    raise GoogleHealthError("Google Health returned invalid JSON", 200) from None
                if not isinstance(payload, dict):
                    raise GoogleHealthError("Google Health returned an invalid object", 200)
                return payload
            if response.status_code == 401 and not refreshed and attempt < 3:
                self._refresh()
                refreshed = True
                continue
            if response.status_code in {429, 500, 502, 503, 504} and attempt < 3:
                delay = number(response.headers.get("Retry-After"))
                if delay is None:
                    try:
                        retry_at = parsedate_to_datetime(response.headers.get("Retry-After", ""))
                        delay = (retry_at - datetime.now(timezone.utc)).total_seconds()
                    except (TypeError, ValueError):
                        delay = 2 ** attempt
                # Respect long server waits by stopping; do not retry sooner than requested.
                if delay > 60:
                    raise GoogleHealthError("Google Health rate limit requires a later sync", response.status_code)
                self.sleep(max(0, delay))
                continue
            try:
                error = response.json().get("error", {})
                details = error.get("details", []) if isinstance(error, dict) else []
                if any(item.get("reason") == "ACCOUNT_NOT_LINKED" for item in details if isinstance(item, dict)):
                    raise GoogleHealthError("此 Google 账号尚未关联 Google Health。请在 Google Health 手机应用中创建资料，或将原 Fitbit 账号迁移到此 Google 账号，然后重新授权。", response.status_code)
            except (ValueError, AttributeError, TypeError):
                pass
            raise GoogleHealthError(f"Google Health request failed (HTTP {response.status_code})", response.status_code)
        raise GoogleHealthError("Google Health request exhausted retries")

    def _pages(self, method, path, key, *, params=None, body=None):
        params, body = dict(params or {}), dict(body or {})
        seen = set()
        while True:
            payload = self.request(method, path, params=dict(params) or None, body=dict(body) or None)
            values = payload.get(key, [])
            if not isinstance(values, list):
                raise GoogleHealthError(f"Google Health returned invalid {key}")
            yield from values
            token = payload.get("nextPageToken")
            if not token:
                return
            if token in seen:
                raise GoogleHealthError("Google Health repeated a pagination token")
            seen.add(token)
            (body if method == "POST" else params)["pageToken"] = token

    def daily_rollup(self, data_type, start, end):
        maximum = 14 if data_type in {"heart-rate", "active-minutes", "total-calories", "calories-in-heart-rate-zone"} else 90
        for first, stop in date_windows(start, end, maximum):
            yield from self._pages("POST", f"users/me/dataTypes/{data_type}/dataPoints:dailyRollUp", "rollupDataPoints", body={
                "range": {"start": _civil(first), "end": _civil(stop)}, "windowSizeDays": 1,
            })

    def reconcile(self, data_type, start, end, filter_field):
        expression = f'{filter_field} >= "{start.isoformat()}" AND {filter_field} < "{end.isoformat()}"'
        return self._pages("GET", f"users/me/dataTypes/{data_type}/dataPoints:reconcile", "dataPoints", params={
            "filter": expression, "pageSize": 25 if data_type in {"sleep", "exercise"} else 1000,
        })

    def profile(self):
        return self.request("GET", "users/me/profile")

    def settings(self):
        return self.request("GET", "users/me/settings")

    def devices(self):
        return list(self._pages("GET", "users/me/pairedDevices", "pairedDevices", params={"pageSize": 100}))


ACTIVITY_COLUMNS = ["date", "steps", "sedentaryMinutes", "activityCalories", "caloriesOut", "lightlyActiveMinutes", "fairlyActiveMinutes", "veryActiveMinutes", "activeZoneMinutes", "activeMinutes", "exerciseExamples", "distance", "floors"]
SLEEP_COLUMNS = ["date", "logId", "isMainSleep", "startTime", "endTime", "duration", "minutesAsleep", "minutesAwake", "minutesToFallAsleep", "minutesAfterWakeup", "timeInBed", "efficiency", "infoCode", "minutesDeep", "minutesREM", "minutesLight", "minutesWakeStages", "minutesAsleepClassic", "minutesRestlessClassic", "minutesAwakeClassic", "sleepScore", "sleepScoreSource"]
ROLLUP_FIELDS = {
    "steps": ("steps", "countSum", "steps", 1),
    "floors": ("floors", "countSum", "floors", 1),
    "distance": ("distance", "millimetersSum", "distance", 1_000_000),
    "total-calories": ("totalCalories", "kcalSum", "caloriesOut", 1),
}


def activity_row(point, data_type):
    day = civil_date(point.get("civilStartTime"))
    if not day:
        return None
    row = {"date": day}
    if data_type in ROLLUP_FIELDS:
        outer, field, column, divisor = ROLLUP_FIELDS[data_type]
        value = number((point.get(outer) or {}).get(field))
        row[column] = value / divisor if value is not None else None
    elif data_type == "active-minutes":
        columns = {"LIGHT": "lightlyActiveMinutes", "MODERATE": "fairlyActiveMinutes", "VIGOROUS": "veryActiveMinutes"}
        for item in (point.get("activeMinutes") or {}).get("activeMinutesRollupByActivityLevel", []):
            column = columns.get(item.get("activityLevel"))
            if column:
                row[column] = integer(item.get("activeMinutesSum"))
        values = [row.get(column) for column in columns.values()]
        # Preserve FitBaus' existing activeMinutes definition: all three levels.
        # A missing activity level is unknown, not an invented zero-minute value.
        row["activeMinutes"] = sum(values) if all(value is not None for value in values) else None
    elif data_type == "active-zone-minutes":
        payload = point.get("activeZoneMinutes") or {}
        values = [integer(payload.get(key)) for key in ("sumInCardioHeartZone", "sumInPeakHeartZone", "sumInFatBurnHeartZone")]
        row["activeZoneMinutes"] = sum(values) if all(value is not None for value in values) else None
    return row


def rhr_row(point):
    payload = point.get("dailyRestingHeartRate") or {}
    day = date_value(payload.get("date"))
    return {"date": day, "resting_heart_rate": integer(payload.get("beatsPerMinute"))} if day else None


def hrv_row(point):
    payload = point.get("dailyHeartRateVariability") or {}
    day = date_value(payload.get("date"))
    return {"date": day, "dailyRmssd": number(payload.get("averageHeartRateVariabilityMilliseconds")), "deepRmssd": number(payload.get("deepSleepRootMeanSquareOfSuccessiveDifferencesMilliseconds"))} if day else None


def estimated_sleep_score(row, goal_minutes=450):
    """Retain the existing local score, excluding unknown component values."""
    asleep, bed = row.get("minutesAsleep"), row.get("timeInBed")
    parts = []
    clamp = lambda value: max(0, min(100, value))
    if asleep is not None and goal_minutes:
        parts.append((clamp(100 * asleep / goal_minutes), .4))
    if row.get("efficiency") is not None:
        parts.append((clamp(row["efficiency"]), .3))
    elif asleep is not None and bed:
        parts.append((clamp(100 * asleep / bed), .3))
    if asleep and row.get("minutesDeep") is not None and row.get("minutesREM") is not None:
        parts.append((clamp(100 * ((row["minutesDeep"] + row["minutesREM"]) / asleep - .25) / .35), .2))
    if row.get("minutesToFallAsleep") is not None and row.get("minutesAwake") is not None:
        parts.append((clamp(100 - .5 * min(row["minutesToFallAsleep"], 60) - .5 * row["minutesAwake"]), .1))
    return round(sum(value * weight for value, weight in parts) / sum(weight for _, weight in parts), 1) if parts else None


def duration_ms(interval):
    try:
        start = datetime.fromisoformat(interval["startTime"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(interval["endTime"].replace("Z", "+00:00"))
        return round((end - start).total_seconds() * 1000)
    except (KeyError, TypeError, ValueError):
        return None


def interval_civil_datetime(interval, boundary):
    """Use civil fields when present, otherwise the recorded UTC offset.

    Reconciled sessions can omit output-only civilStartTime/civilEndTime.
    Each endpoint has its own offset, so DST and travel remain accurate.
    """
    civil = civil_datetime(interval.get("civil" + boundary.title() + "Time"))
    if civil:
        return civil
    stamp, offset = interval.get(boundary + "Time"), interval.get(boundary + "UtcOffset")
    if not isinstance(stamp, str) or not isinstance(offset, str):
        return None
    if not re.fullmatch(r"-?\d+(?:\.\d{1,9})?s", offset):
        return None
    try:
        moment = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        if moment.tzinfo is None:
            return None
        local_zone = timezone(timedelta(seconds=float(offset[:-1])))
        return moment.astimezone(local_zone).replace(tzinfo=None).isoformat(timespec="seconds")
    except (ValueError, OverflowError):
        return None


def sleep_row(point, goal_minutes=450):
    payload = point.get("sleep") or {}
    interval, summary = payload.get("interval") or {}, payload.get("summary") or {}
    start_time = interval_civil_datetime(interval, "start")
    end_time = interval_civil_datetime(interval, "end")
    if not end_time:
        return None
    row = {"date": end_time[:10], "logId": point.get("dataPointName") or point.get("name"),
           "isMainSleep": (payload.get("metadata") or {}).get("mainSleep"),
           "startTime": start_time, "endTime": end_time,
           "duration": duration_ms(interval)}
    for source, target in {"minutesAsleep": "minutesAsleep", "minutesAwake": "minutesAwake", "minutesToFallAsleep": "minutesToFallAsleep", "minutesAfterWakeUp": "minutesAfterWakeup", "minutesInSleepPeriod": "timeInBed"}.items():
        row[target] = integer(summary.get(source))
    row["efficiency"] = round(100 * row["minutesAsleep"] / row["timeInBed"]) if row["minutesAsleep"] is not None and row["timeInBed"] else None
    stage_columns = {"DEEP": "minutesDeep", "REM": "minutesREM", "LIGHT": "minutesLight", "ASLEEP": "minutesAsleepClassic", "RESTLESS": "minutesRestlessClassic", "AWAKE": "minutesWakeStages" if payload.get("type") == "STAGES" else "minutesAwakeClassic"}
    for stage in summary.get("stagesSummary", []):
        column = stage_columns.get(stage.get("type"))
        if column:
            row[column] = integer(stage.get("minutes"))
    row["sleepScore"] = estimated_sleep_score(row, goal_minutes)
    row["sleepScoreSource"] = "local_estimate" if row["sleepScore"] is not None else None
    return row
