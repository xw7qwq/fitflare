import unittest
from datetime import date

from common.google_health import GoogleHealthError, METRICS_SCOPE
from common.google_health_extras import NUTRITION_SCOPE, fetch_extras


class FakeClient:
    def __init__(self, payloads=None, scopes=None):
        self.payloads = payloads or {}
        self.scopes = {METRICS_SCOPE, NUTRITION_SCOPE} if scopes is None else scopes
        self.calls = []

    def reconcile(self, kind, start, end, field):
        self.calls.append((kind, start, end, field))
        result = self.payloads.get(kind, [])
        if isinstance(result, Exception):
            raise result
        return result


class GoogleHealthExtrasTests(unittest.TestCase):
    def test_units_dates_and_vital_meanings(self):
        client = FakeClient({
            "weight": [{"weight": {"sampleTime": {"physicalTime": "2026-10-22T18:00:00Z", "utcOffset": "28800s"}, "weightGrams": 71250}}],
            "body-fat": [{"bodyFat": {"sampleTime": {"civilTime": {"date": {"year": 2026, "month": 10, "day": 23}}}, "percentage": 18.5}}],
            "daily-oxygen-saturation": [{"dailyOxygenSaturation": {"date": {"year": 2026, "month": 10, "day": 23}, "averagePercentage": 97, "lowerBoundPercentage": 95, "upperBoundPercentage": 99}}],
            "daily-sleep-temperature-derivations": [
                {"dailySleepTemperatureDerivations": {"date": {"year": 2026, "month": 10, "day": 22}, "nightlyTemperatureCelsius": 33.2}},
                {"dailySleepTemperatureDerivations": {"date": {"year": 2026, "month": 10, "day": 23}, "nightlyTemperatureCelsius": 33.2, "baselineTemperatureCelsius": 33.2}},
            ],
        })
        result = fetch_extras(client, date(2026, 10, 1), date(2026, 10, 24))
        self.assertEqual(result["weight_log_recent"]["data"]["weight"][0]["weight"], 71.25)
        self.assertEqual(result["weight_log_recent"]["data"]["weight"][0]["date"], "2026-10-23")
        self.assertEqual(result["fat_log_recent"]["data"]["fat"][0]["fat"], 18.5)
        oxygen = result["spo2_today"]["data"]["spo2"][0]["value"]
        self.assertEqual(oxygen["avg"], 97)
        self.assertNotIn("min", oxygen)
        temperature = result["skin_temperature_recent"]["data"]["tempSkin"]
        self.assertEqual(temperature, [{"dateTime": "2026-10-23", "value": {"nightlyRelative": 0}}])

    def test_nutrition_uses_today_and_does_not_invent_missing_energy(self):
        def interval(day):
            return {"civilStartTime": {"date": {"year": 2026, "month": 10, "day": day}}}
        client = FakeClient({
            "nutrition-log": [
                {"nutritionLog": {"interval": interval(22), "foodDisplayName": "Yesterday", "energy": {"kcal": 100}}},
                {"nutritionLog": {"interval": interval(23), "foodDisplayName": "Breakfast", "energy": {"kcal": 200}}},
                {"nutritionLog": {"interval": interval(23), "foodDisplayName": "Unknown"}},
            ],
            "hydration-log": [{"hydrationLog": {"interval": interval(23), "amountConsumed": {"milliliters": 250, "userProvidedUnit": "CUP_US"}}}],
        })
        result = fetch_extras(client, date(2026, 10, 1), date(2026, 10, 24))
        self.assertIsNone(result["food_log_today"]["data"]["summary"]["calories"])
        self.assertEqual(len(result["food_log_today"]["data"]["foods"]), 2)
        self.assertEqual(result["water_log_today"]["data"]["summary"]["water"], 250)
        water_call = next(call for call in client.calls if call[0] == "hydration-log")
        self.assertEqual(water_call[1:3], (date(2026, 10, 23), date(2026, 10, 24)))

    def test_partial_consent_and_failure_are_not_empty_success(self):
        client = FakeClient({"weight": GoogleHealthError("denied", 403)}, {METRICS_SCOPE})
        result = fetch_extras(client, date(2026, 10, 1), date(2026, 10, 24))
        self.assertFalse(result["weight_log_recent"]["ok"])
        self.assertEqual(result["weight_log_recent"]["status"], 403)
        self.assertTrue(result["fat_log_recent"]["ok"])
        self.assertTrue(result["food_log_today"]["skipped"])
        self.assertFalse(any(call[0] == "nutrition-log" for call in client.calls))

    def test_missing_logs_and_incomplete_water_keep_null(self):
        empty = fetch_extras(FakeClient(), date(2026, 10, 1), date(2026, 10, 24))
        self.assertIsNone(empty["food_log_today"]["data"]["summary"]["calories"])
        self.assertIsNone(empty["water_log_today"]["data"]["summary"]["water"])
        interval = {"civilStartTime": {"date": {"year": 2026, "month": 10, "day": 23}}}
        client = FakeClient({"hydration-log": [
            {"hydrationLog": {"interval": interval, "amountConsumed": {"milliliters": 250}}},
            {"hydrationLog": {"interval": interval}},
        ]})
        incomplete = fetch_extras(client, date(2026, 10, 1), date(2026, 10, 24))
        self.assertIsNone(incomplete["water_log_today"]["data"]["summary"]["water"])
        client.payloads["hydration-log"] = [{"hydrationLog": {"interval": interval, "amountConsumed": {"milliliters": 0}}}]
        zero = fetch_extras(client, date(2026, 10, 1), date(2026, 10, 24))
        self.assertEqual(zero["water_log_today"]["data"]["summary"]["water"], 0)


if __name__ == "__main__":
    unittest.main()
