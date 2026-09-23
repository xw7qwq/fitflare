"""Offline Google Health v4 wire fixtures; no account credentials or network."""
import json
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

from common import profile_paths
from common.google_health import (
    API_BASE, ACTIVITY_SCOPE, METRICS_SCOPE, PROFILE_SCOPE, SETTINGS_SCOPE, SLEEP_SCOPE,
    GoogleHealthClient, GoogleHealthError, activity_row, hrv_row, require_legacy_fitbit_tokens,
    rhr_row, sleep_row, profile_fetch_lock,
)
from fetch.fetch_google_health import (
    CORE_SPECS, atomic_json, csv_rows, merge_csv, read_json, start_date_for, sync_profile,
)
from fetch import fetch_all
from common.google_health_auth import GOOGLE_HEALTH_SCOPES
from common.google_health_auth import profile_migration_lock, GoogleHealthAuthError


DAY = {"year": 2026, "month": 10, "day": 22}


def response(status=200, payload=None, headers=None):
    result = Mock(status_code=status, headers=headers or {})
    result.json.return_value = payload or {}
    return result


class GoogleHealthDataTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        # All account data lives under profiles/<configured owner>/.
        self.account = self.root / "profiles" / profile_paths.owner_profile_id()
        self.tokens = self.account / "auth" / "tokens.json"
        self.tokens.parent.mkdir(parents=True)
        self.tokens.write_text(json.dumps({"oauth_type": "google", "access_token": "synthetic-access",
                                          "granted_scopes": [ACTIVITY_SCOPE]}))
        patcher = patch.object(profile_paths, "ROOT_DIR", str(self.root))
        patcher.start()
        self.addCleanup(patcher.stop)

    def client(self, replies, refresh=None):
        session = Mock()
        session.request.side_effect = replies
        client = GoogleHealthClient(tokens_path=str(self.tokens), session=session, sleep=Mock(), refresh=refresh)
        return client, session

    def test_rollup_uses_civil_days_and_api_limits(self):
        client, session = self.client([response() for _ in range(3)])
        list(client.daily_rollup("total-calories", date(2026, 9, 1), date(2026, 10, 1)))
        calls = session.request.call_args_list
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[0].args, ("POST", API_BASE + "/users/me/dataTypes/total-calories/dataPoints:dailyRollUp"))
        self.assertEqual(calls[0].kwargs["json"], {"windowSizeDays": 1, "range": {
            "start": {"date": {"year": 2026, "month": 9, "day": 1}},
            "end": {"date": {"year": 2026, "month": 9, "day": 15}}}})
        self.assertEqual(calls[-1].kwargs["json"]["range"]["end"]["date"]["day"], 1)

    def test_reconcile_sleep_uses_end_date_and_follows_pages(self):
        client, session = self.client([response(payload={"dataPoints": [{"dataPointName": "one"}], "nextPageToken": "p2"}),
                                       response(payload={"dataPoints": [{"dataPointName": "two"}]})])
        points = list(client.reconcile("sleep", date(2026, 10, 21), date(2026, 10, 23), "sleep.interval.civil_end_time"))
        self.assertEqual(len(points), 2)
        self.assertNotIn("pageToken", session.request.call_args_list[0].kwargs["params"])
        args = session.request.call_args.kwargs["params"]
        self.assertEqual(args["pageSize"], 25)
        self.assertEqual(args["pageToken"], "p2")
        self.assertEqual(args["filter"], 'sleep.interval.civil_end_time >= "2026-10-21" AND sleep.interval.civil_end_time < "2026-10-23"')

    def test_repeated_page_token_fails_without_loop(self):
        client, session = self.client([response(payload={"nextPageToken": "same"})] * 2)
        with self.assertRaises(GoogleHealthError):
            list(client.devices())
        self.assertEqual(session.request.call_count, 2)

    def test_expired_token_refreshes_once_and_never_calls_fitbit(self):
        refresh = Mock(return_value="synthetic-refreshed")
        client, session = self.client([response(401), response(payload={"age": 30})], refresh)
        self.assertEqual(client.profile(), {"age": 30})
        refresh.assert_called_once_with()
        self.assertEqual(session.request.call_args.kwargs["headers"]["Authorization"], "Bearer synthetic-refreshed")
        self.assertTrue(all(call.args[1].startswith(API_BASE) for call in session.request.call_args_list))

    def test_persistent_unauthorized_and_forbidden_fail(self):
        for status in (401, 403):
            refresh = Mock(return_value="synthetic-refreshed")
            client, session = self.client([response(status), response(status)], refresh)
            with self.assertRaises(GoogleHealthError) as caught:
                client.profile()
            self.assertEqual(caught.exception.status, status)
            self.assertLessEqual(session.request.call_count, 2)

    def test_retry_is_bounded_and_respects_retry_after(self):
        client, session = self.client([response(429, headers={"Retry-After": "2"})] * 4)
        with self.assertRaises(GoogleHealthError):
            client.profile()
        self.assertEqual(session.request.call_count, 4)
        self.assertEqual(client.sleep.call_count, 3)
        client.sleep.assert_called_with(2)
        client, session = self.client([response(429, headers={"Retry-After": "900"})])
        with self.assertRaises(GoogleHealthError):
            client.profile()
        client.sleep.assert_not_called()
        self.assertEqual(session.request.call_count, 1)

    def test_refresh_failure_is_captured_as_data_error(self):
        refresh = Mock(side_effect=GoogleHealthAuthError("请重新授权", "invalid_grant", 401))
        client, _ = self.client([response(401)], refresh)
        with self.assertRaises(GoogleHealthError) as caught:
            client.profile()
        self.assertEqual(str(caught.exception), "请重新授权")
        self.assertEqual(caught.exception.status, 401)

    def test_unlinked_account_has_actionable_error_without_echoing_response(self):
        client, _ = self.client([response(400, {"error": {"message": "must-not-echo-upstream-text", "details": [{"reason": "ACCOUNT_NOT_LINKED"}]}})])
        with self.assertRaises(GoogleHealthError) as caught:
            client.profile()
        self.assertIn("Google Health 手机应用", str(caught.exception))
        self.assertNotIn("must-not-echo", str(caught.exception))

    def test_legacy_guard_blocks_google_token(self):
        with self.assertRaisesRegex(RuntimeError, "Google Health"):
            require_legacy_fitbit_tokens(str(self.tokens))
        self.tokens.write_text(json.dumps({"access_token": "synthetic-legacy"}))
        require_legacy_fitbit_tokens(str(self.tokens))

    def test_activity_converts_units_without_summing_duplicate_streams(self):
        base = {"civilStartTime": {"date": DAY}}
        self.assertEqual(activity_row({**base, "distance": {"millimetersSum": "4567890"}}, "distance")["distance"], 4.56789)
        self.assertEqual(activity_row({**base, "steps": {"countSum": "7300"}}, "steps")["steps"], 7300)
        point = {**base, "activeMinutes": {"activeMinutesRollupByActivityLevel": [
            {"activityLevel": "LIGHT", "activeMinutesSum": "30"}, {"activityLevel": "MODERATE", "activeMinutesSum": "10"},
            {"activityLevel": "VIGOROUS", "activeMinutesSum": "5"}]}}
        self.assertEqual(activity_row(point, "active-minutes")["activeMinutes"], 45)
        azm = {**base, "activeZoneMinutes": {"sumInFatBurnHeartZone": "10", "sumInCardioHeartZone": "20", "sumInPeakHeartZone": "2"}}
        self.assertEqual(activity_row(azm, "active-zone-minutes")["activeZoneMinutes"], 32)
        self.assertIsNone(activity_row(base, "steps")["steps"])
        self.assertIsNone(activity_row(base, "active-minutes")["activeMinutes"])

    def test_daily_metrics_use_official_schema_and_missing_stays_unknown(self):
        self.assertEqual(rhr_row({"dailyRestingHeartRate": {"date": DAY, "beatsPerMinute": "57"}})["resting_heart_rate"], 57)
        row = hrv_row({"dailyHeartRateVariability": {"date": DAY, "averageHeartRateVariabilityMilliseconds": 42.5}})
        self.assertEqual(row["dailyRmssd"], 42.5)
        self.assertIsNone(row["deepRmssd"])

    def test_sleep_civil_wake_day_and_physical_duration_across_dst(self):
        fixture = {"dataPointName": "users/me/dataTypes/sleep/dataPoints/synthetic", "sleep": {
            "type": "STAGES", "metadata": {"mainSleep": True}, "interval": {
                "startTime": "2026-11-01T03:00:00Z", "endTime": "2026-11-01T12:00:00Z",
                "civilStartTime": {"date": {"year": 2026, "month": 10, "day": 31}, "time": {"hours": 23}},
                "civilEndTime": {"date": {"year": 2026, "month": 11, "day": 1}, "time": {"hours": 7}}},
            "summary": {"minutesAsleep": "480", "minutesAwake": "60", "minutesInSleepPeriod": "540",
                "minutesToFallAsleep": "5", "minutesAfterWakeUp": "2",
                "stagesSummary": [{"type": "DEEP", "minutes": "80"}, {"type": "REM", "minutes": "100"}, {"type": "AWAKE", "minutes": "60"}]}}}
        row = sleep_row(fixture)
        self.assertEqual(row["date"], "2026-11-01")
        self.assertEqual(row["startTime"], "2026-10-31T23:00:00")
        self.assertEqual(row["duration"], 9 * 60 * 60 * 1000)
        self.assertEqual(row["minutesAfterWakeup"], 2)
        self.assertEqual(row["minutesDeep"], 80)
        self.assertEqual(row["sleepScoreSource"], "local_estimate")

    def test_merge_preserves_history_and_missing_metrics(self):
        path = str(self.account / "csv" / "fitbit_activity.csv")
        merge_csv(path, [{"date": "2026-10-20", "steps": 123, "sedentaryMinutes": 300}], ["date", "steps", "sedentaryMinutes"])
        merge_csv(path, [{"date": "2026-10-20", "steps": None}, {"date": "2026-10-21", "steps": 0}], ["date", "steps"])
        rows, _ = csv_rows(path)
        self.assertEqual(rows[0]["steps"], "123")
        self.assertEqual(rows[0]["sedentaryMinutes"], "300")
        self.assertEqual(rows[1]["steps"], "0")
        self.assertEqual(rows[1]["sedentaryMinutes"], "")

    def test_sleep_without_civil_fields_uses_recorded_offsets_across_dst(self):
        row = sleep_row({"sleep": {"interval": {
            "startTime": "2026-11-01T03:00:00Z", "startUtcOffset": "-14400s",
            "endTime": "2026-11-01T12:00:00Z", "endUtcOffset": "-18000s"}}})
        self.assertEqual(row["date"], "2026-11-01")
        self.assertEqual(row["startTime"], "2026-10-31T23:00:00")
        self.assertEqual(row["endTime"], "2026-11-01T07:00:00")
        self.assertEqual(row["duration"], 9 * 60 * 60 * 1000)

    def test_sleep_without_civil_fields_keeps_local_wake_date(self):
        row = sleep_row({"sleep": {"interval": {
            "startTime": "2026-10-21T15:00:00Z", "startUtcOffset": "28800s",
            "endTime": "2026-10-21T23:00:00Z", "endUtcOffset": "28800s"}}})
        self.assertEqual(row["date"], "2026-10-22")
        self.assertEqual(row["endTime"], "2026-10-22T07:00:00")
        self.assertIsNone(row["isMainSleep"])

    def test_sleep_without_civil_fields_rejects_missing_or_invalid_timezone(self):
        for offset in (None, "invalid", "NaNs", "86400s"):
            with self.subTest(offset=offset):
                self.assertIsNone(sleep_row({"sleep": {"interval": {
                    "endTime": "2026-10-21T23:00:00Z", "endUtcOffset": offset}}}))

    def test_sync_sleep_without_civil_fields_updates_csv_and_cursor(self):
        client = self.fake_sync_client(GOOGLE_HEALTH_SCOPES)
        point = {"dataPointName": "synthetic-offset-sleep", "sleep": {"interval": {
            "startTime": "2026-10-21T15:00:00Z", "startUtcOffset": "28800s",
            "endTime": "2026-10-21T23:00:00Z", "endUtcOffset": "28800s"},
            "summary": {"minutesAsleep": "450", "minutesInSleepPeriod": "480"}}}
        client.reconcile.side_effect = lambda metric, *_: [point] if metric == "sleep" else []
        self.assertEqual(sync_profile(None, client=client, today=date(2026, 10, 22)), 0)
        rows, _ = csv_rows(str(self.account / "csv" / "fitbit_sleep.csv"))
        self.assertEqual(rows[0]["date"], "2026-10-22")
        self.assertEqual(rows[0]["minutesAsleep"], "450")
        state = read_json(str(self.account / "cache" / "google_health_sync.json"))
        self.assertEqual(state["completed_through"]["sleep"], "2026-10-22")

    def test_sleep_migration_replaces_duplicate_provider_ids_only_on_returned_days(self):
        path = str(self.account / "csv" / "fitbit_sleep.csv")
        merge_csv(path, [{"date": "2026-10-20", "logId": "old20"}, {"date": "2026-10-21", "logId": "old21"}], ["date", "logId"], sleep=True)
        merge_csv(path, [{"date": "2026-10-21", "logId": "google21"}, {"date": "2026-10-21", "logId": "nap21"}], ["date", "logId"], sleep=True)
        merge_csv(path, [], ["date", "logId"], sleep=True)
        self.assertEqual([row["logId"] for row in csv_rows(path)[0]], ["old20", "google21", "nap21"])

    def fake_sync_client(self, scopes):
        client = Mock()
        client.scopes = set(scopes)
        client.tokens = {"health_user_id": "synthetic"}
        client.profile.return_value = {"membershipStartDate": DAY}
        client.settings.return_value = {"timeZone": "Asia/Taipei", "languageLocale": "zh-TW"}
        client.daily_rollup.return_value = []
        client.reconcile.return_value = []
        client.devices.return_value = []
        return client

    def test_partial_scopes_do_not_erase_existing_data_or_claim_complete(self):
        path = str(self.account / "csv" / "fitbit_sleep.csv")
        merge_csv(path, [{"date": "2026-10-20", "minutesAsleep": 400}], ["date", "minutesAsleep"], sleep=True)
        before = Path(path).read_bytes()
        client = self.fake_sync_client([PROFILE_SCOPE])
        result = sync_profile(None, client=client, today=date(2026, 10, 22))
        self.assertEqual(result, 2)
        self.assertEqual(Path(path).read_bytes(), before)
        client.daily_rollup.assert_not_called()
        client.reconcile.assert_not_called()
        snapshot = read_json(str(self.account / "cache" / "fitbit_profile_snapshot.json"))
        self.assertEqual(snapshot["sync_status"], "partial")
        self.assertTrue(snapshot["endpoints"]["google_health_sleep"]["reason"].startswith("missing_scope:"))

    def test_failed_later_chunk_keeps_checkpoint_and_data_for_resume(self):
        client = self.fake_sync_client([ACTIVITY_SCOPE, PROFILE_SCOPE])
        client.profile.return_value = {"membershipStartDate": {"year": 2026, "month": 9, "day": 1}}
        def rollup(metric, first, stop):
            if metric == "total-calories" and first >= date(2026, 9, 15):
                raise GoogleHealthError("synthetic upstream failure", 503)
            if metric == "total-calories":
                return [{"civilStartTime": {"date": {"year": 2026, "month": 9, "day": 2}}, "totalCalories": {"kcalSum": 2000}}]
            return []
        client.daily_rollup.side_effect = rollup
        self.assertEqual(sync_profile(None, client=client, today=date(2026, 9, 30)), 2)
        cursor = read_json(str(self.account / "cache" / "google_health_sync.json"))["completed_through"]
        self.assertEqual(cursor["total-calories"], "2026-09-14")
        self.assertEqual(cursor["steps"], "2026-09-30")
        rows, _ = csv_rows(str(self.account / "csv" / "fitbit_activity.csv"))
        self.assertEqual(rows[0]["caloriesOut"], "2000.0")
        self.assertEqual(start_date_for(str(self.account / "csv" / "fitbit_activity.csv"), "total-calories", "caloriesOut", cursor, date(2026, 9, 1)), date(2026, 9, 8))

    def test_unsupported_goals_keep_historical_payload_with_stale_marker(self):
        snapshot = {"endpoints": {"sleep_goal": {"ok": True, "fetched_at": "2026-10-01", "data": {"goal": {"minDuration": 480}}}}}
        atomic_json(str(self.account / "cache" / "fitbit_profile_snapshot.json"), snapshot)
        client = self.fake_sync_client([PROFILE_SCOPE])
        sync_profile(None, client=client, today=date(2026, 10, 22))
        entry = read_json(str(self.account / "cache" / "fitbit_profile_snapshot.json"))["endpoints"]["sleep_goal"]
        self.assertFalse(entry["ok"])
        self.assertTrue(entry["stale"])
        self.assertEqual(entry["cached_provider"], "fitbit")
        self.assertEqual(entry["data"]["goal"]["minDuration"], 480)

    def test_full_consent_empty_response_completes_without_inventing_csv_rows(self):
        client = self.fake_sync_client(GOOGLE_HEALTH_SCOPES)
        self.assertEqual(sync_profile(None, client=client, today=date(2026, 10, 22)), 0)
        self.assertFalse((self.account / "csv" / "fitbit_activity.csv").exists())
        snapshot = read_json(str(self.account / "cache" / "fitbit_profile_snapshot.json"))
        self.assertEqual(snapshot["sync_status"], "complete")
        self.assertIsNone(snapshot["endpoints"]["food_log_today"]["data"]["summary"]["calories"])

    def test_today_snapshot_does_not_present_legacy_metrics_as_fresh(self):
        path = str(self.account / "csv" / "fitbit_activity.csv")
        merge_csv(path, [{"date": "2026-10-22", "steps": 123, "activityCalories": 456}], ["date", "steps", "activityCalories"])
        client = self.fake_sync_client(GOOGLE_HEALTH_SCOPES)
        sync_profile(None, client=client, today=date(2026, 10, 22))
        snapshot = read_json(str(self.account / "cache" / "fitbit_profile_snapshot.json"))
        self.assertFalse(snapshot["endpoints"]["today_activity_summary"]["ok"])
        client.daily_rollup.side_effect = lambda metric, first, stop: [{"civilStartTime": {"date": DAY}, "steps": {"countSum": "789"}}] if metric == "steps" else []
        sync_profile(None, client=client, today=date(2026, 10, 22))
        summary = read_json(str(self.account / "cache" / "fitbit_profile_snapshot.json"))["endpoints"]["today_activity_summary"]["data"]["summary"]
        self.assertEqual(summary["steps"], 789)
        self.assertNotIn("activityCalories", summary)
        self.assertIsNone(summary["caloriesOut"])

    def test_malformed_dates_do_not_advance_checkpoint(self):
        client = self.fake_sync_client([ACTIVITY_SCOPE, PROFILE_SCOPE])
        client.daily_rollup.return_value = [{"steps": {"countSum": "123"}}]
        self.assertEqual(sync_profile(None, client=client, today=date(2026, 10, 22)), 2)
        self.assertFalse((self.account / "cache" / "google_health_sync.json").exists())

    def test_fetch_all_uses_only_google_script_for_migrated_profile(self):
        with patch.object(fetch_all, "build_dashboard_cache"), \
             patch.object(fetch_all, "run_script", return_value=(0, None)) as run, \
             patch("sys.argv", ["fetch_all.py"]):
            self.assertEqual(fetch_all.main(), 0)
        self.assertEqual([Path(call.args[0]).name for call in run.call_args_list], ["fetch_google_health.py"])

    def test_fetch_all_keeps_legacy_provider_until_google_authorization(self):
        self.tokens.write_text(json.dumps({"access_token": "synthetic-legacy"}))
        with patch.object(fetch_all, "build_dashboard_cache"), \
             patch.object(fetch_all, "run_script", return_value=(0, None)) as run, \
             patch("sys.argv", ["fetch_all.py"]):
            self.assertEqual(fetch_all.main(), 0)
        self.assertEqual([Path(call.args[0]).name for call in run.call_args_list], [
            "fetch_steps.py", "fetch_rhr_data.py", "fetch_hrv_data.py", "fetch_sleep_data.py", "fetch_profile_snapshot.py"])

    def test_cli_fetch_lock_excludes_migration_and_is_shared_by_children(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FITBAUS_FETCH_LOCK_HELD", None)
            with profile_fetch_lock(None):
                self.assertEqual(os.environ["FITBAUS_FETCH_LOCK_HELD"], str(self.tokens))
                with self.assertRaises(GoogleHealthAuthError):
                    with profile_migration_lock(self.tokens):
                        pass
                with profile_fetch_lock(None):
                    pass  # Child inherits the parent's existing lock.
            self.assertNotIn("FITBAUS_FETCH_LOCK_HELD", os.environ)
            with profile_migration_lock(self.tokens):
                pass

    def test_sleep_unknown_fields_preserve_matching_legacy_session(self):
        path = str(self.account / "csv" / "fitbit_sleep.csv")
        merge_csv(path, [{"date": "2026-10-22", "logId": "legacy123", "startTime": "2026-10-21T23:00:00.000", "endTime": "2026-10-22T07:00:00.000", "minutesDeep": 75}], ["date", "logId", "startTime", "endTime", "minutesDeep"], sleep=True)
        merge_csv(path, [{"date": "2026-10-22", "logId": "google123", "startTime": "2026-10-21T23:00:00", "endTime": "2026-10-22T07:00:00", "minutesDeep": None, "minutesAsleep": 420}], ["date", "logId", "startTime", "endTime", "minutesDeep", "minutesAsleep"], sleep=True)
        rows, _ = csv_rows(path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["logId"], "google123")
        self.assertEqual(rows[0]["minutesDeep"], "75")
        self.assertEqual(rows[0]["minutesAsleep"], "420")


if __name__ == "__main__":
    unittest.main()
