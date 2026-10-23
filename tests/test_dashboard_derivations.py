"""Derived dashboard rows: non-wear days, exercise times, retained lifetime stats, cache versioning."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from common import dashboard_cache, profile_paths
from common.dashboard_cache import _build_activity_tables, _build_trend, _merge_daily_rows, _aggregate_rows, _window_values
from fetch.fetch_google_health import exercise_rows


def activity(date, steps, active=0, calories=1721):
    return {"date": date, "steps": steps, "active_minutes": active, "very_active_minutes": 0, "fairly_active_minutes": 0,
            "lightly_active_minutes": active, "active_zone_minutes": 0, "calories_out": calories}


class DashboardDerivationTests(unittest.TestCase):
    def test_non_wear_days_are_missing_not_zero(self):
        rows = _merge_daily_rows([activity("2026-10-02", 0), activity("2026-10-03", 8000, 120, 2500)], [], [], [], {})
        self.assertTrue(rows[0]["non_wear"])
        self.assertIsNone(rows[0]["steps"])
        self.assertIsNone(rows[0]["calories_out"])
        self.assertNotIn("non_wear", rows[1])
        self.assertEqual(_aggregate_rows(rows, "month")[0]["steps"], 8000)

    def test_zero_steps_with_sleep_or_heart_data_is_a_real_zero(self):
        rows = _merge_daily_rows([activity("2026-10-02", 0)], [], [], [{"date": "2026-10-02", "rhr": 60}], {})
        self.assertEqual(rows[0]["steps"], 0)
        self.assertNotIn("non_wear", rows[0])

    def test_exercise_without_civil_time_uses_recorded_offset(self):
        rows = exercise_rows([{"name": "x", "exercise": {"displayName": "Walk", "interval": {
            "startTime": "2026-10-22T23:30:00Z", "startUtcOffset": "28800s",
            "endTime": "2026-10-23T00:00:00Z", "endUtcOffset": "28800s"}}}])
        self.assertEqual(rows[0]["startTime"], "2026-10-23T07:30:00")
        _, table = _build_activity_tables({"endpoints": {"activity_log_list": {"ok": True, "data": {"activities": rows}}}})
        self.assertEqual(table[0]["date"], "2026-10-23")

    def test_retained_lifetime_stats_and_missing_goal(self):
        snapshot = {"endpoints": {
            "today_activity_summary": {"ok": True, "data": {"summary": {"steps": 14308}}},
            "lifetime_stats": {"ok": False, "skipped": True, "stale": True, "cached_at": "2026-10-23T08:05:43", "data": {
                "lifetime": {"total": {"steps": 2286594, "distance": 1626.78, "floors": 557}},
                "best": {"total": {"steps": {"date": "2026-02-20", "value": 27274}}}}},
        }}
        section, _ = _build_activity_tables(snapshot)
        self.assertEqual(section["metrics"][0]["detail"], "今日 14308 步 · 未设置目标")
        self.assertEqual(section["metrics"][4]["value"], 2286594)
        self.assertEqual(section["lifetime"]["best"]["steps"], {"value": 27274, "date": "2026-02-20"})
        self.assertEqual(section["lifetime"]["cached_at"], "2026-10-23T08:05:43")

    def test_trend_windows_count_recorded_values_and_never_overlap(self):
        rows = [{"v": value} for value in (1, 2, 3, 4, 5, 6, 7)] + [{"v": None}, {"v": None}] + [{"v": value} for value in range(10, 17)]
        self.assertEqual(_window_values(rows, "v", 7), [10, 11, 12, 13, 14, 15, 16])
        self.assertEqual(_window_values(rows, "v", 7, offset=7), [1, 2, 3, 4, 5, 6, 7])
        self.assertEqual(_window_values(rows, "v", 7, offset=20), [])

    def test_trend_direction_is_numeric_and_assessment_applies_polarity(self):
        rising = [{"rhr": 60}] * 7 + [{"rhr": 63}] * 7
        trend = _build_trend(rising, "rhr", higher_is_better=False, tolerance=1)
        self.assertEqual((trend["direction"], trend["assessment"], trend["delta"]), ("up", "worsening", 3.0))
        trend = _build_trend(rising, "rhr", higher_is_better=True, tolerance=1)
        self.assertEqual((trend["direction"], trend["assessment"]), ("up", "improving"))
        flat = _build_trend(rising, "rhr", higher_is_better=False, tolerance=5)
        self.assertEqual((flat["direction"], flat["assessment"]), ("flat", "stable"))
        self.assertEqual(_build_trend([{"rhr": 60}], "rhr", False, 1)["assessment"], "stable")

    def test_outdated_cache_schema_is_rebuilt(self):
        with tempfile.TemporaryDirectory() as root, patch.object(profile_paths, "ROOT_DIR", root):
            cache = Path(root, "profiles", "Demo", "cache")
            cache.mkdir(parents=True)
            (cache / "dashboard.json").write_text(json.dumps({"schema_version": 1, "charts": {}}))
            payload = dashboard_cache.load_dashboard_cache("Demo")
            self.assertEqual(payload["schema_version"], dashboard_cache.CACHE_SCHEMA_VERSION)


if __name__ == "__main__":
    unittest.main()
