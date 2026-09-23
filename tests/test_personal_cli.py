"""Offline regression for CLI scope: never discover and sync other accounts."""
import contextlib
import io
import os
import unittest
from unittest.mock import patch

from fetch import fetch_all


class PersonalCliTests(unittest.TestCase):
    def run_sync(self, arguments=(), return_code=0):
        with patch.dict(os.environ, {'FITFLARE_PROFILE_ID': 'Demo', 'FITBIT_PROFILE': 'Hidden'}), \
             patch('sys.argv', ['fetch_all.py', *arguments]), \
             patch.object(fetch_all, 'run_script', return_value=(return_code, None)) as runner, \
             patch.object(fetch_all, 'build_dashboard_cache') as cache, \
             patch('os.listdir', side_effect=AssertionError('Account discovery is forbidden')), \
             contextlib.redirect_stdout(io.StringIO()):
            result = fetch_all.main()
            return result, runner.call_args_list, cache.call_args_list

    def test_default_sync_targets_only_configured_owner(self):
        result, calls, caches = self.run_sync()
        self.assertEqual(result, 0)
        self.assertEqual(len(calls), 5)
        self.assertTrue(all(call.kwargs['extra_args'] == ['--profile', 'Demo'] for call in calls))
        self.assertEqual(caches[0].args, ('Demo',))

    def test_legacy_batch_options_are_rejected_before_running(self):
        for arguments in (['--all-profiles'], ['--profiles', 'Demo', 'Hidden']):
            with self.subTest(arguments=arguments), contextlib.redirect_stderr(io.StringIO()), \
                 self.assertRaises(SystemExit) as raised:
                self.run_sync(arguments)
            self.assertEqual(raised.exception.code, 2)

    def test_failed_fetch_is_reported_and_stop_on_error_is_honored(self):
        result, calls, _ = self.run_sync(['--stop-on-error'], return_code=1)
        self.assertEqual(result, 1)
        self.assertEqual(len(calls), 1)

    def test_explicit_maintenance_override_stays_one_account(self):
        result, calls, caches = self.run_sync(['--profile', 'Archive'])
        self.assertEqual(result, 0)
        self.assertTrue(all(call.kwargs['extra_args'] == ['--profile', 'Archive'] for call in calls))
        self.assertEqual(caches[0].args, ('Archive',))
