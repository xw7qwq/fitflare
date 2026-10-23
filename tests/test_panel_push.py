"""Panel push contracts; all data is synthetic and the panel is a local stub."""
import csv
import json
import os
import tempfile
import threading
import unittest
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ['FITFLARE_AUTO_SYNC_ENABLED'] = 'false'
from backend import sync
from common import panel_push, profile_paths
from tests.fixture import make_fixture

TOKEN = 'test-only-token'


def write_csv(path, rows):
    with open(path, 'w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def write_history(directory, days=450):
    start = date(2025, 1, 1)
    dates = [str(start + timedelta(days=i)) for i in range(days)]
    write_csv(directory / 'fitbit_activity.csv', [
        dict(date=day, steps=6000 + i * 7 % 3000, sedentaryMinutes=600, activityCalories=500, caloriesOut=2200,
             lightlyActiveMinutes=150, fairlyActiveMinutes=20, veryActiveMinutes=15, activeZoneMinutes=25, activeMinutes=35)
        for i, day in enumerate(dates)])
    write_csv(directory / 'fitbit_sleep.csv', [
        dict(date=day, isMainSleep='True', minutesAsleep=400 + i % 40, minutesAwake=30, timeInBed=450, efficiency=90,
             sleepScore=78 + i % 9, minutesDeep=70, minutesREM=90, minutesLight=240, minutesWakeStages=30,
             minutesToFallAsleep=10, minutesAfterWakeup=5, startTime=f'{day}T23:30:00', endTime=f'{day}T07:00:00')
        for i, day in enumerate(dates)])
    write_csv(directory / 'fitbit_hrv.csv', [dict(date=day, dailyRmssd=45 + i % 6, deepRmssd=50) for i, day in enumerate(dates)])
    write_csv(directory / 'fitbit_rhr.csv', [dict(date=day, resting_heart_rate=60 + i % 4) for i, day in enumerate(dates)])


class StubPanel(HTTPServer):
    """Answers each POST with the next scripted (status, payload) and records it."""

    def __init__(self, responses):
        super().__init__(('127.0.0.1', 0), StubHandler)
        self.responses = list(responses)
        self.requests = []

    @property
    def url(self):
        return f'http://127.0.0.1:{self.server_address[1]}/api/v1/ingest/health'


class StubHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers['Content-Length']))
        self.server.requests.append(dict(path=self.path, headers=dict(self.headers), body=body))
        status, payload = self.server.responses.pop(0)
        data = payload.encode() if isinstance(payload, str) else json.dumps(payload).encode()
        self.send_response(status)
        if status in (301, 302, 307, 308):
            self.send_header('Location', '/elsewhere')
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


class PanelPushTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        make_fixture(self.root / 'profiles')
        old_cwd = os.getcwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, old_cwd)
        patcher = patch.object(profile_paths, 'ROOT_DIR', str(self.root))
        patcher.start()
        self.addCleanup(patcher.stop)
        env = patch.dict(os.environ, {'FITFLARE_PROFILE_ID': 'Demo'})
        env.start()
        self.addCleanup(env.stop)
        for name in ('FITFLARE_PANEL_INGEST_TOKEN', 'FITFLARE_PANEL_INGEST_URL'):
            os.environ.pop(name, None)

    def stub(self, responses):
        server = StubPanel(responses)
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server

    def test_snapshot_matches_the_panel_schema(self):
        write_history(self.root / 'profiles/Demo/csv')
        snapshot = panel_push.build_snapshot('Demo')
        self.assertEqual(snapshot['schema_version'], 1)
        self.assertEqual(snapshot['source'], 'health')
        self.assertRegex(snapshot['generated_at'], r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$')
        self.assertEqual([card['key'] for card in snapshot['metrics']][:3], ['sleep_score', 'sleep_hours', 'steps'])
        daily, weekly = snapshot['daily'], snapshot['weekly']
        self.assertEqual(len(daily['points']), panel_push.DAILY_POINTS)
        self.assertEqual(daily['points'][-1]['date'], str(date(2025, 1, 1) + timedelta(days=449)))
        self.assertLessEqual(len(weekly['points']), panel_push.WEEKLY_POINTS)
        self.assertRegex(weekly['points'][-1]['period'], r'^\d{4}-W\d{2}$')
        keys = {metric['key'] for metric in daily['metrics']}
        self.assertTrue({'sleep_hours', 'steps', 'hrv', 'rhr'} <= keys)
        self.assertTrue(all({'key', 'label', 'unit', 'tone'} <= set(metric) for metric in daily['metrics']))
        self.assertEqual(set(snapshot['sleep'][0]), {'date', 'score', 'hours', 'deep', 'rem', 'light', 'awake'})
        text = panel_push.encode(snapshot).decode()
        self.assertNotIn(str(self.root), text)
        self.assertNotIn('tokens', text)

    def test_snapshot_without_synced_files_is_still_valid(self):
        snapshot = panel_push.build_snapshot('Demo')
        self.assertEqual(snapshot['daily'], {'metrics': [], 'points': []})
        self.assertIsInstance(snapshot['metrics'], list)

    def test_non_finite_numbers_become_null(self):
        self.assertEqual(panel_push._finite({'a': float('nan'), 'b': [float('inf'), 1.5]}), {'a': None, 'b': [None, 1.5]})

    def test_ingest_url_must_be_https(self):
        self.assertIsNone(panel_push.panel_settings())
        with patch.dict(os.environ, {'FITFLARE_PANEL_INGEST_TOKEN': TOKEN}):
            self.assertEqual(panel_push.panel_settings()['url'], panel_push.DEFAULT_URL)
            for url in ('http://lucius7.dev/api/v1/ingest/health', 'ftp://127.0.0.1/x', 'https://'):
                with patch.dict(os.environ, {'FITFLARE_PANEL_INGEST_URL': url}), self.assertRaises(panel_push.PanelPushError):
                    panel_push.panel_settings()
            with patch.dict(os.environ, {'FITFLARE_PANEL_INGEST_URL': 'http://127.0.0.1:8787/api/v1/ingest/health'}):
                self.assertEqual(panel_push.panel_settings()['token'], TOKEN)

    def test_send_posts_json_with_bearer_token(self):
        server = self.stub([(200, {'data': {'stored': True, 'bytes': 10, 'received_at': '2026-10-23T00:00:00Z'}})])
        snapshot = panel_push.build_snapshot('Demo')
        result = panel_push.send_snapshot(snapshot, server.url, TOKEN)
        self.assertEqual(result['stored'], True)
        request = server.requests[0]
        self.assertEqual(request['path'], '/api/v1/ingest/health')
        self.assertEqual(request['headers']['Authorization'], f'Bearer {TOKEN}')
        self.assertEqual(request['headers']['Content-Type'], 'application/json')
        self.assertEqual(json.loads(request['body']), snapshot)

    def test_client_errors_fail_fast_and_server_errors_retry(self):
        server = self.stub([(401, {'error': {'code': 'unauthorized', 'message': 'bad token'}})])
        with self.assertRaises(panel_push.PanelPushError) as caught:
            panel_push.send_snapshot({}, server.url, TOKEN, sleep=lambda seconds: None)
        self.assertIn('401', str(caught.exception))
        self.assertIn('unauthorized', str(caught.exception))
        self.assertNotIn(TOKEN, str(caught.exception))
        self.assertEqual(len(server.requests), 1)

        server = self.stub([(503, {}), (429, {}), (200, {'data': {'stored': False}})])
        delays = []
        self.assertEqual(panel_push.send_snapshot({}, server.url, TOKEN, sleep=delays.append), {'stored': False})
        self.assertEqual((len(server.requests), delays), (3, [2, 4]))

    def test_redirects_and_non_json_answers_are_errors(self):
        server = self.stub([(301, {}), (200, '<!doctype html>')])
        with self.assertRaises(panel_push.PanelPushError) as caught:
            panel_push.send_snapshot({}, server.url, TOKEN, sleep=lambda seconds: None)
        self.assertIn('301', str(caught.exception))
        self.assertEqual(len(server.requests), 1)
        with self.assertRaises(panel_push.PanelPushError) as caught:
            panel_push.send_snapshot({}, server.url, TOKEN, sleep=lambda seconds: None)
        self.assertIn('not JSON', str(caught.exception))

    def test_push_is_due_after_new_data_and_once_per_utc8_day(self):
        now = datetime(2026, 10, 23, 15, 30, tzinfo=timezone.utc)  # 23:30 in UTC+8
        self.assertTrue(panel_push.push_due({}, 0, now))
        state = dict(last_success_day='2026-10-23', source_mtime=100.0, failures=0)
        self.assertFalse(panel_push.push_due(state, 100.0, now))
        self.assertTrue(panel_push.push_due(state, 101.0, now))
        self.assertTrue(panel_push.push_due(state, 100.0, now + timedelta(minutes=31)))  # midnight in UTC+8

        failed = dict(state, failures=1, last_failure_ts=now.timestamp())
        self.assertFalse(panel_push.push_due(failed, 101.0, now + timedelta(seconds=60)))
        self.assertTrue(panel_push.push_due(failed, 101.0, now + timedelta(seconds=301)))
        failed['failures'] = 30
        self.assertFalse(panel_push.push_due(failed, 101.0, now + timedelta(hours=5)))
        self.assertTrue(panel_push.push_due(failed, 101.0, now + timedelta(hours=6, seconds=1)))

    def test_scheduler_hook_records_success_and_backs_off_after_failure(self):
        logs = []
        self.assertFalse(panel_push.push_if_due('Demo', log=lambda *args: logs.append(args)))
        self.assertFalse(os.path.exists(self.root / 'profiles/Demo/cache/panel_push.json'))

        server = self.stub([(200, {'data': {'stored': True, 'bytes': 1}}), (500, {}), (500, {}), (500, {})])
        with patch.dict(os.environ, {'FITFLARE_PANEL_INGEST_TOKEN': TOKEN, 'FITFLARE_PANEL_INGEST_URL': server.url}):
            self.assertTrue(panel_push.push_if_due('Demo', log=lambda *args: logs.append(args)))
            state = panel_push.load_state('Demo')
            self.assertEqual((state['failures'], state['stored']), (0, True))
            self.assertFalse(panel_push.push_if_due('Demo', log=lambda *args: logs.append(args)))
            self.assertEqual(len(server.requests), 1)

            write_history(self.root / 'profiles/Demo/csv', days=20)
            with patch.object(panel_push.time, 'sleep'):
                self.assertFalse(panel_push.push_if_due('Demo', log=lambda *args: logs.append(args)))
            state = panel_push.load_state('Demo')
            self.assertEqual(state['failures'], 1)
            self.assertIn('500', state['last_error'])
            self.assertEqual(logs[-1][1], 'ERROR')
            self.assertFalse(panel_push.push_if_due('Demo', log=lambda *args: logs.append(args)))
            self.assertEqual(len(server.requests), 4)
        self.assertNotIn(TOKEN, json.dumps(state))

    def test_scheduler_hook_never_raises(self):
        logs = []
        with patch.dict(os.environ, {'FITFLARE_PANEL_INGEST_TOKEN': TOKEN, 'FITFLARE_PANEL_INGEST_URL': 'http://example.com/'}):
            self.assertFalse(panel_push.push_if_due('Demo', log=lambda *args: logs.append(args)))
        self.assertIn('https', logs[0][0])

    def test_sync_loop_pushes_after_each_cycle(self):
        stop = MagicMock()
        stop.is_set.return_value = False
        stop.wait.return_value = True
        with patch.object(sync, 'AUTO_SYNC_STARTUP_DELAY_SECONDS', 0), patch.object(sync, 'auto_sync_stop_event', stop), \
                patch.object(sync, 'run_auto_sync_cycle') as cycle, patch.object(panel_push, 'push_if_due') as hook:
            sync._auto_sync_loop()
        cycle.assert_called_once_with()
        hook.assert_called_once_with('Demo')


if __name__ == '__main__':
    unittest.main()
