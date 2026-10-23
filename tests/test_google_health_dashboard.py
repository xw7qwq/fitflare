"""Prevent old cache metadata from misrepresenting migrated health data."""
import unittest
from unittest.mock import patch

from common.dashboard_cache import _build_account_tables, _build_lifestyle_tables, _profile_from_snapshot
from common.google_health_auth import GOOGLE_HEALTH_SCOPES


class GoogleHealthDashboardTests(unittest.TestCase):
    def test_active_google_scopes_override_legacy_snapshot(self):
        with patch('common.dashboard_cache._read_json', return_value={
            'oauth_type': 'google', 'scope': GOOGLE_HEALTH_SCOPES[0],
        }):
            profile = _profile_from_snapshot('Demo', {'token_scope': ['activity', 'sleep']})
        self.assertEqual(profile['scopes'], [GOOGLE_HEALTH_SCOPES[0]])
        self.assertEqual(profile['requested_scopes'], list(GOOGLE_HEALTH_SCOPES))
        self.assertEqual(profile['oauth_type'], 'google')

    def test_missing_daily_total_does_not_use_one_food_or_water_record(self):
        snapshot = {'endpoints': {
            'food_log_today': {'ok': True, 'data': {'summary': {'calories': None}, 'foods': [{'calories': 500}]}},
            'water_log_today': {'ok': True, 'data': {'summary': {'water': None}, 'water': [{'amount': 250}]}},
        }}
        section, _ = _build_lifestyle_tables(snapshot)
        self.assertIsNone(section['metrics'][1]['value'])
        self.assertIsNone(section['metrics'][3]['value'])

    def test_unsupported_old_data_is_labelled_historical(self):
        snapshot = {'endpoints': {'badges': {'ok': False, 'skipped': True, 'stale': True,
            'reason': 'unavailable_in_google_health_v4', 'data': {'badges': [{'name': 'Old badge'}]}}}}
        _, _, _, _, endpoints = _build_account_tables({}, snapshot)
        self.assertEqual(endpoints[0]['status'], '新接口不提供，保留历史缓存')
