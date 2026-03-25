import os
import json
import time
import requests
import subprocess
import pandas as pd
from datetime import datetime, timedelta
import sys
import argparse

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from auth.refresh_token import refresh_token
from common.profile_paths import (
    get_active_profile,
    tokens_file_for,
    csv_path_for,
    ensure_dirs_for_csv,
)
from common.rate_limit import wait_until_next_hour_with_countdown, wait_seconds_with_countdown
from common.fitbit_profile import get_member_since_date

parser = argparse.ArgumentParser(add_help=False)
parser.add_argument("--profile", default=None)
args, _unknown = parser.parse_known_args()
PROFILE_ID = get_active_profile(args.profile)

TOKENS_FILE = tokens_file_for(PROFILE_ID)
CSV_FILE = csv_path_for(PROFILE_ID, "fitbit_activity.csv")
os.environ["FITBIT_TOKENS_FILE"] = TOKENS_FILE
os.environ["FITBIT_PROFILE"] = PROFILE_ID or ""
ensure_dirs_for_csv(CSV_FILE)

CHUNK_DAYS = 90
FALLBACK_START_DATE = datetime(2018, 8, 1)
RATE_LIMIT_DELAY = 2
RECENT_SUMMARY_DAYS = 14
CSV_COLUMNS = [
    "date",
    "steps",
    "sedentaryMinutes",
    "activityCalories",
    "caloriesOut",
    "lightlyActiveMinutes",
    "fairlyActiveMinutes",
    "veryActiveMinutes",
    "activeZoneMinutes",
    "activeMinutes",
    "exerciseExamples",
]
TIME_SERIES_RESOURCES = {
    "steps": "steps",
    "sedentaryMinutes": "minutesSedentary",
    "activityCalories": "activityCalories",
    "caloriesOut": "calories",
    "lightlyActiveMinutes": "minutesLightlyActive",
    "fairlyActiveMinutes": "minutesFairlyActive",
    "veryActiveMinutes": "minutesVeryActive",
    "activeZoneMinutes": "activeZoneMinutes",
}


def get_access_token():
    with open(TOKENS_FILE) as f:
        return json.load(f)["access_token"]


def load_last_date():
    if os.path.exists(CSV_FILE):
        try:
            df = pd.read_csv(CSV_FILE, usecols=["date"])
            if df.empty or "date" not in df.columns:
                return None
            dates = pd.to_datetime(df["date"], errors="coerce").dropna()
            if dates.empty:
                return None
            d = dates.max().date()
            return datetime(d.year, d.month, d.day)
        except Exception:
            return None
    return None


def daterange_chunks(start, end, delta_days):
    while start <= end:
        chunk_end = min(start + timedelta(days=delta_days - 1), end)
        yield start.strftime("%Y-%m-%d"), chunk_end.strftime("%Y-%m-%d")
        start += timedelta(days=delta_days)



def choose_fitbit_period(days: int) -> str:
    d = max(1, int(days))
    if d <= 1:
        return "1d"
    if d <= 7:
        return "7d"
    if d <= 30:
        return "30d"
    if d <= 90:
        return "3m"
    if d <= 180:
        return "6m"
    if d <= 366:
        return "1y"
    return "max"



def run_refresh_script():
    script_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "auth", "refresh_token.py"))
    script_dir = os.path.dirname(script_path)
    subprocess.run([sys.executable, script_path], cwd=script_dir, check=True)
    return get_access_token()



def request_json(url: str, token: str, label: str):
    headers = {"Authorization": f"Bearer {token}"}
    for _attempt in range(3):
        try:
            res = requests.get(url, headers=headers, timeout=30)
        except requests.exceptions.Timeout:
            print(f"Timeout for {label}")
            continue
        except requests.exceptions.RequestException as e:
            print(f"Request error for {label}: {e}")
            continue

        print(f"Response {res.status_code} for {label}")

        try:
            remaining = int(res.headers.get("fitbit-rate-limit-remaining", "1"))
        except Exception:
            remaining = 1
        try:
            reset_s = int(res.headers.get("fitbit-rate-limit-reset", "0"))
        except Exception:
            reset_s = 0
        if remaining <= 0 and reset_s > 0:
            wait_for = max(reset_s + 1, 1)
            print(f"Rate-limit headers indicate reset in {reset_s}s.")
            wait_seconds_with_countdown(wait_for, context="Header reset")

        if res.status_code == 200:
            return res.json(), token
        if res.status_code == 429:
            if reset_s > 0:
                wait_for = max(reset_s + 1, 1)
                print("429 rate limit. Using header-provided reset.")
                wait_seconds_with_countdown(wait_for, context="Header reset")
            else:
                wait_until_next_hour_with_countdown(f"Rate limited by Fitbit ({label})")
            continue
        if res.status_code == 401:
            print("Access token expired. Refreshing...")
            try:
                token = refresh_token()
                headers = {"Authorization": f"Bearer {token}"}
                continue
            except Exception as e:
                print(f"Failed to refresh token: {e}")
                try:
                    token = run_refresh_script()
                    headers = {"Authorization": f"Bearer {token}"}
                    print("Retrying request after running refresh_token.py...")
                    continue
                except Exception as se:
                    print(f"Failed to run refresh_token.py: {se}")
                    return None, token
        if res.status_code in (404, 500):
            print(f"⚠️  No/partial data for {label} ({res.status_code})")
            return {}, token
        print(f"Error: {res.status_code} - {res.text}")
        return None, token
    return None, token



def fetch_timeseries_period(resource: str, end_str: str, period: str, token: str):
    url = f"https://api.fitbit.com/1/user/-/activities/{resource}/date/{end_str}/{period}.json"
    return request_json(url, token, f"{resource} period {period} ending {end_str}")



def fetch_timeseries_chunk(resource: str, start_str: str, end_str: str, token: str):
    url = f"https://api.fitbit.com/1/user/-/activities/{resource}/date/{start_str}/{end_str}.json"
    return request_json(url, token, f"{resource} {start_str} to {end_str}")



def fetch_daily_activity_summary(date_str: str, token: str):
    url = f"https://api.fitbit.com/1/user/-/activities/date/{date_str}.json"
    return request_json(url, token, f"daily summary {date_str}")



def _safe_num(value):
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None



def _to_int(value):
    n = _safe_num(value)
    if n is None:
        return None
    return int(round(n))



def _extract_series(payload: dict, resource: str):
    if not isinstance(payload, dict):
        return []

    candidates = [
        f"activities-{resource}",
        f"activities-{resource.replace('Minutes', '-minutes').replace('Zone', '-zone')}",
        f"activities-{resource.replace('Active', 'active').replace('Minutes', 'Minutes')}",
        "activities-active-zone-minutes",
        "activities-activeZoneMinutes",
    ]
    for key in candidates:
        value = payload.get(key)
        if isinstance(value, list):
            return value

    for value in payload.values():
        if isinstance(value, list) and value and isinstance(value[0], dict) and "dateTime" in value[0]:
            return value
    return []



def _extract_timeseries_value(resource: str, entry: dict):
    raw = entry.get("value")
    if isinstance(raw, (int, float, str)):
        return _to_int(raw)
    if isinstance(raw, dict):
        if resource == "activeZoneMinutes":
            direct = raw.get("activeZoneMinutes")
            if direct is not None:
                return _to_int(direct)
            total = 0
            found = False
            for key in [
                "fatBurnActiveZoneMinutes",
                "cardioActiveZoneMinutes",
                "peakActiveZoneMinutes",
                "fatBurnMinutes",
                "cardioMinutes",
                "peakMinutes",
            ]:
                value = _to_int(raw.get(key))
                if value is not None:
                    total += value
                    found = True
            return total if found else None
        for key in ["value", resource, "minutes", "total"]:
            if key in raw:
                return _to_int(raw.get(key))
    return None



def merge_timeseries_payload(data_by_date: dict, column_name: str, resource: str, payload: dict):
    series = _extract_series(payload, resource)
    for entry in series:
        date_str = entry.get("dateTime")
        if not date_str:
            continue
        bucket = data_by_date.setdefault(date_str, {"date": date_str})
        value = _extract_timeseries_value(resource, entry)
        if value is not None:
            bucket[column_name] = value



def extract_activity_examples(summary_payload: dict):
    activities = summary_payload.get("activities") or []
    names = []
    for activity in activities:
        if not isinstance(activity, dict):
            continue
        name = (
            activity.get("activityName")
            or activity.get("activity")
            or activity.get("name")
            or activity.get("logType")
        )
        if name and name not in names:
            names.append(str(name).strip())
        if len(names) >= 3:
            break
    return "、".join(names)



def enrich_from_daily_summary(data_by_date: dict, date_str: str, summary_payload: dict):
    bucket = data_by_date.setdefault(date_str, {"date": date_str})
    summary = summary_payload.get("summary") or {}

    field_map = {
        "steps": summary.get("steps"),
        "sedentaryMinutes": summary.get("sedentaryMinutes"),
        "activityCalories": summary.get("activityCalories"),
        "caloriesOut": summary.get("caloriesOut"),
        "lightlyActiveMinutes": summary.get("lightlyActiveMinutes"),
        "fairlyActiveMinutes": summary.get("fairlyActiveMinutes"),
        "veryActiveMinutes": summary.get("veryActiveMinutes"),
    }
    for key, value in field_map.items():
        ivalue = _to_int(value)
        if ivalue is not None:
            bucket[key] = ivalue

    azm = summary.get("activeZoneMinutes")
    azm_total = None
    if isinstance(azm, dict):
        azm_total = _to_int(azm.get("totalMinutes") or azm.get("activeZoneMinutes"))
        if azm_total is None:
            total = 0
            found = False
            for key in ["fatBurnActiveZoneMinutes", "cardioActiveZoneMinutes", "peakActiveZoneMinutes"]:
                value = _to_int(azm.get(key))
                if value is not None:
                    total += value
                    found = True
            azm_total = total if found else None
    elif isinstance(azm, list):
        total = 0
        found = False
        for item in azm:
            if not isinstance(item, dict):
                continue
            value = _to_int(item.get("minutes") or item.get("value"))
            if value is not None:
                total += value
                found = True
        azm_total = total if found else None
    else:
        azm_total = _to_int(azm)
    if azm_total is not None:
        bucket["activeZoneMinutes"] = azm_total

    examples = extract_activity_examples(summary_payload)
    if examples:
        bucket["exerciseExamples"] = examples



def finalize_rows(data_by_date: dict):
    rows = []
    for date_str in sorted(data_by_date.keys()):
        bucket = data_by_date[date_str]
        row = {"date": date_str}
        for column in CSV_COLUMNS:
            if column == "date":
                continue
            row[column] = bucket.get(column)
        active_parts = [row.get("lightlyActiveMinutes"), row.get("fairlyActiveMinutes"), row.get("veryActiveMinutes")]
        if any(value is not None for value in active_parts):
            row["activeMinutes"] = sum(int(value or 0) for value in active_parts)
        active_zone_value = row.get("activeZoneMinutes")
        if active_zone_value is None or active_zone_value == "" or pd.isna(active_zone_value):
            fairly = _to_int(row.get("fairlyActiveMinutes")) or 0
            very = _to_int(row.get("veryActiveMinutes")) or 0
            if fairly or very:
                row["activeZoneMinutes"] = fairly + very * 2
        rows.append(row)
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=CSV_COLUMNS)
    for column in CSV_COLUMNS:
        if column not in df.columns:
            df[column] = pd.NA
    return df[CSV_COLUMNS]



def merge_and_save(combined: pd.DataFrame, df_new: pd.DataFrame, end_label: str):
    initial_count = len(combined)
    if not df_new.empty and "date" in df_new.columns:
        df_new = df_new.copy()
        df_new["date"] = pd.to_datetime(df_new["date"])
        combined = pd.concat([combined, df_new], ignore_index=True)
        combined = combined.drop_duplicates(subset=["date"], keep="last").sort_values("date")
    for column in CSV_COLUMNS:
        if column not in combined.columns:
            combined[column] = pd.NA
    combined = combined[CSV_COLUMNS]
    combined.to_csv(CSV_FILE, index=False)
    print(f"Saved activity data to {CSV_FILE} up to {end_label}")
    final_count = len(combined)
    print("All available activity data fetched and saved.")
    print(f"Added {final_count - initial_count} new activity records. Total: {final_count}")
    return combined



def build_recent_refresh_df(combined: pd.DataFrame, end_date: datetime, token: str):
    if combined.empty or "date" not in combined.columns:
        return pd.DataFrame(columns=CSV_COLUMNS), token

    recent_start = pd.to_datetime(end_date.date() - timedelta(days=RECENT_SUMMARY_DAYS - 1))
    recent_rows = combined[combined["date"] >= recent_start].copy()
    if recent_rows.empty:
        return pd.DataFrame(columns=CSV_COLUMNS), token

    data_by_date = {}
    for row in recent_rows.to_dict(orient="records"):
        date_value = row.get("date")
        if pd.isna(date_value):
            continue
        date_str = date_value.strftime("%Y-%m-%d") if hasattr(date_value, "strftime") else str(date_value)[:10]
        bucket = {"date": date_str}
        for column in CSV_COLUMNS:
            if column == "date":
                continue
            value = row.get(column)
            if pd.isna(value):
                continue
            bucket[column] = value
        data_by_date[date_str] = bucket

    if not data_by_date:
        return pd.DataFrame(columns=CSV_COLUMNS), token

    start_dt = datetime.strptime(min(data_by_date.keys()), "%Y-%m-%d")
    for column_name, resource in TIME_SERIES_RESOURCES.items():
        payload, token = fetch_timeseries_chunk(resource, start_dt.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d"), token)
        if payload is None:
            continue
        merge_timeseries_payload(data_by_date, column_name, resource, payload)
        time.sleep(0.1)
    token = enrich_recent_summaries(data_by_date, start_dt, end_date, token)
    return finalize_rows(data_by_date), token



def load_existing_dataframe():
    if os.path.exists(CSV_FILE):
        combined = pd.read_csv(CSV_FILE)
        if "date" in combined.columns:
            combined["date"] = pd.to_datetime(combined["date"], errors="coerce")
        else:
            combined = pd.DataFrame(columns=CSV_COLUMNS)
    else:
        combined = pd.DataFrame(columns=CSV_COLUMNS)
    for column in CSV_COLUMNS:
        if column not in combined.columns:
            combined[column] = pd.NA
    return combined[CSV_COLUMNS]



def enrich_recent_summaries(data_by_date: dict, start_date: datetime, end_date: datetime, token: str):
    summary_start = max(start_date, end_date - timedelta(days=RECENT_SUMMARY_DAYS - 1))
    current = summary_start
    while current <= end_date:
        date_str = current.strftime("%Y-%m-%d")
        payload, token = fetch_daily_activity_summary(date_str, token)
        if payload is not None:
            enrich_from_daily_summary(data_by_date, date_str, payload)
        time.sleep(0.2)
        current += timedelta(days=1)
    return token



def fetch_period_dataset(start_date: datetime, end_date: datetime, token: str):
    pending_days = (end_date.date() - start_date.date()).days + 1
    period = choose_fitbit_period(pending_days)
    end_str = end_date.strftime("%Y-%m-%d")
    print(f"Fetching activity data using period {period} ending {end_str}...")
    print(f"Fetching {start_date.strftime('%Y-%m-%d')} to {end_str}...")

    data_by_date = {}
    steps_loaded = False
    for column_name, resource in TIME_SERIES_RESOURCES.items():
        payload, token = fetch_timeseries_period(resource, end_str, period, token)
        if payload is None:
            if column_name == "steps":
                return None, token
            continue
        merge_timeseries_payload(data_by_date, column_name, resource, payload)
        if column_name == "steps" and data_by_date:
            steps_loaded = True
        time.sleep(0.15)

    if not steps_loaded:
        return None, token

    token = enrich_recent_summaries(data_by_date, start_date, end_date, token)
    df = finalize_rows(data_by_date)
    if df.empty:
        return None, token
    df["date"] = pd.to_datetime(df["date"])
    df = df[df["date"] >= pd.to_datetime(start_date.date())]
    return df, token



def fetch_chunked_dataset(start_date: datetime, end_date: datetime, token: str):
    combined_chunks = []
    total_chunks = 0
    successful_chunks = 0
    failed_chunks = 0

    for start_str, end_str in daterange_chunks(start_date, end_date, CHUNK_DAYS):
        total_chunks += 1
        print(f"Fetching activity data {start_str} to {end_str}...")
        data_by_date = {}
        steps_loaded = False

        for column_name, resource in TIME_SERIES_RESOURCES.items():
            payload, token = fetch_timeseries_chunk(resource, start_str, end_str, token)
            if payload is None:
                if column_name == "steps":
                    failed_chunks += 1
                    print(f"Failed to fetch core steps data for {start_str} to {end_str}")
                    data_by_date = {}
                    break
                continue
            merge_timeseries_payload(data_by_date, column_name, resource, payload)
            if column_name == "steps" and data_by_date:
                steps_loaded = True
            time.sleep(0.15)

        if not steps_loaded or not data_by_date:
            continue

        chunk_start = datetime.strptime(start_str, "%Y-%m-%d")
        chunk_end = datetime.strptime(end_str, "%Y-%m-%d")
        token = enrich_recent_summaries(data_by_date, chunk_start, chunk_end, token)
        df_chunk = finalize_rows(data_by_date)
        if df_chunk.empty:
            failed_chunks += 1
            continue
        combined_chunks.append(df_chunk)
        successful_chunks += 1
        print(f"Saved activity chunk in memory for {start_str} to {end_str}")
        time.sleep(RATE_LIMIT_DELAY)

    if not combined_chunks:
        return None, token, total_chunks, successful_chunks, failed_chunks

    df = pd.concat(combined_chunks, ignore_index=True)
    return df, token, total_chunks, successful_chunks, failed_chunks



def main():
    last_date = load_last_date()
    if last_date:
        start_date = last_date + timedelta(days=1)
        source = f"CSV (last date {last_date.strftime('%Y-%m-%d')})"
    else:
        ms = get_member_since_date(PROFILE_ID)
        if ms:
            start_date = ms
            source = "Fitbit profile.memberSince"
        else:
            start_date = FALLBACK_START_DATE
            source = f"fallback constant {FALLBACK_START_DATE.strftime('%Y-%m-%d')}"

    print(f"Starting activity data fetch from {start_date.strftime('%Y-%m-%d')} (source: {source})")
    end_date = datetime.now() - timedelta(days=1)
    combined = load_existing_dataframe()
    token = get_access_token()

    if start_date.date() > end_date.date():
        print("Up to date for new dates. Refreshing recent summary fields instead...")
        df_recent, token = build_recent_refresh_df(combined, end_date, token)
        if not df_recent.empty:
            merge_and_save(combined, df_recent, end_date.strftime("%Y-%m-%d"))
        else:
            print("✅ No new activity dates to fetch.")
        return

    print(
        f"📊 Fetching activity data (steps + calories + active minutes + active zone + examples) from "
        f"{start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}"
    )
    print("⏳ This may take a few minutes...")

    df_period, token = fetch_period_dataset(start_date, end_date, token)
    if df_period is not None:
        merge_and_save(combined, df_period, end_date.strftime("%Y-%m-%d"))
        return

    token = get_access_token()
    df_chunked, token, total_chunks, successful_chunks, failed_chunks = fetch_chunked_dataset(start_date, end_date, token)
    if df_chunked is None:
        if successful_chunks == 0:
            print("✅ No new data available - all data is already up to date.")
        else:
            print(f"⚠️ Partial success: {successful_chunks}/{total_chunks} chunks fetched.")
        return

    merge_and_save(combined, df_chunked, end_date.strftime("%Y-%m-%d"))
    if failed_chunks > 0:
        print(f"⚠️ Partial success: {successful_chunks}/{total_chunks} chunks fetched.")


if __name__ == "__main__":
    main()
