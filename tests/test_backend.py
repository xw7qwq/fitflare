"""Offline public-read-only contracts; all data is synthetic and temporary."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ['FITFLARE_AUTO_SYNC_ENABLED'] = 'false'
with patch.dict(os.environ, {'FITFLARE_DATA_ACCESS': 'public'}):
    from server import create_app
from backend import jobs, sync
from common import profile_paths
from tests.fixture import make_fixture


class BackendTests(unittest.TestCase):
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
        env = patch.dict(os.environ, {'FITFLARE_PROFILE_ID': 'Demo', 'FITBIT_CLIENT_ID': '',
                                      'FITBIT_CLIENT_SECRET': '', 'FITBIT_TOKENS_FILE': ''})
        env.start()
        self.addCleanup(env.stop)
        self.config = dict(OWNER_PROFILE_ID='Demo', TESTING=True, DATA_ACCESS_MODE='public',
                           PROFILES_DIR=str(self.root / 'profiles'))
        self.app = create_app(self.config)
        self.client = self.app.test_client()
        jobs.fetch_jobs.clear()


    def test_all_read_surfaces_only_expose_the_owner(self):
        for suffix in ('', '/dashboard', '/catalog', '/overview', '/coverage', '/metrics', '/correlations', '/series/daily', '/datasets', '/sections', '/tables', '/snapshot', '/snapshot/endpoints', '/charts/overview-trend.svg'):
            with self.subTest(suffix=suffix):
                self.assertEqual(self.client.get('/api/public/v1/profiles/Hidden'+suffix).status_code, 404)
        self.assertEqual(self.client.get('/api/dashboard/Hidden').status_code, 404)
        self.assertEqual(self.client.get('/api/tables/Hidden/sleep').status_code, 404)
        self.assertEqual([p['name'] for p in self.client.get('/api/profiles').json], ['Demo'])
        self.assertEqual([p['id'] for p in self.client.get('/api/profile-summaries').json], ['Demo'])
        self.assertEqual(self.client.get('/api/public/v1/profiles').json['meta']['count'], 1)

    def test_tables_are_optional_and_server_paginated(self):
        self.assertNotIn('tables', self.client.get('/api/dashboard/Demo?tables=none').json)
        response = self.client.get('/api/tables/Demo/sleep?offset=20&limit=20').json
        self.assertEqual(response['meta'], dict(total=55, offset=20, limit=20, count=20))
        self.assertEqual(len(response['rows']), 20)
        self.assertEqual(self.client.get('/api/tables/Demo/sleep?offset=100000').json['rows'], [])
        self.assertEqual(self.client.get('/api/tables/Demo/unknown').status_code, 404)
        self.assertEqual(self.client.get('/api/tables/Demo/sleep?limit=9999').json['meta']['limit'], 100)

    def test_docs_and_all_sample_public_routes_render(self):
        self.assertEqual(self.client.get('/api/public/v1/docs').status_code, 200)
        text = self.client.get('/api/public/v1/docs').get_data(as_text=True)
        self.assertIn('/api/public/v1/me', text)
        self.assertNotIn('Demo', text)
        for path in ('/api/public/v1/openapi.json', '/api/public/v1/profiles/Demo/dashboard', '/api/public/v1/profiles/Demo/tables/sleep', '/api/public/v1/profiles/Demo/series/daily', '/api/public/v1/profiles/Demo/charts/overview-trend.svg'):
            self.assertEqual(self.client.get(path).status_code, 200, path)

    def test_profile_lock_excludes_parallel_syncs(self):
        first = sync._acquire_profile_fetch_lock('Demo', 'test-1')
        self.assertIsNotNone(first)
        try:
            self.assertIsNone(sync._acquire_profile_fetch_lock('Demo', 'test-2'))
        finally:
            sync._release_profile_fetch_lock(first)
        second = sync._acquire_profile_fetch_lock('Demo', 'test-3')
        self.assertIsNotNone(second)
        sync._release_profile_fetch_lock(second)

    def test_no_scheduler_starts_on_import(self):
        self.assertIsNone(sync.auto_sync_thread)

    def test_background_sync_and_cli_discovery_only_touch_owner(self):
        for profile in ('Demo', 'Hidden'):
            (self.root / f'profiles/{profile}/auth/tokens.json').write_text(json.dumps({'refresh_token':'synthetic'}))
        self.assertEqual(profile_paths.list_profiles(), ['Demo'])
        self.assertEqual(sync._discover_syncable_profiles(), ['Demo'])
        with patch.object(sync, 'AUTO_SYNC_ENABLED', True), \
             patch.object(sync, '_profile_due_for_auto_sync', return_value=True), \
             patch.object(sync, '_run_auto_sync_for_profile') as run:
            sync.auto_sync_stop_event.clear()
            sync.run_auto_sync_cycle()
            run.assert_called_once_with('Demo')
        for operation in (sync._acquire_profile_fetch_lock, sync.run_fetch_script):
            with self.assertRaises(ValueError):
                operation('Hidden', 'untrusted-job')
        with self.assertRaises(ValueError):
            sync._run_auto_sync_for_profile('Hidden')
        self.assertFalse((self.root / 'profiles/Hidden/cache/.fetch.lock').exists())
        with patch.dict(os.environ, {'FITBIT_TOKENS_FILE': '/unrelated/tokens.json'}):
            env = sync._prepare_fetch_env('Demo')
        self.assertEqual(env['FITBIT_TOKENS_FILE'], str((self.root / 'profiles/Demo/auth/tokens.json').resolve()))
        self.assertEqual(env['FITFLARE_PROFILE_ID'], 'Demo')

    def test_public_reads_ignore_legacy_sessions_and_never_expose_paths(self):
        self.client.set_cookie('fitflare_session', 'legacy-admin-cookie')
        for path in ('/api/dashboard', '/api/dashboard/Demo', '/api/public/v1/me/dashboard'):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertNotIn('/private/', response.get_data(as_text=True))
            self.assertNotIn('files', response.json)
            self.assertNotIn('Set-Cookie', response.headers)
            self.assertIn('no-store', response.headers['Cache-Control'])

    def test_all_management_routes_are_removed_and_writes_cannot_mutate_data(self):
        before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        paths = ('/api/admin/session', '/api/admin/login', '/api/admin/logout', '/api/account/setup',
                 '/api/authorize', '/api/authorize/Demo', '/api/authorize-exchange',
                 '/api/fetch-data', '/api/fetch-jobs', '/api/fetch-status/old', '/api/cancel-fetch/old',
                 '/api/rebuild-dashboard', '/api/create-profile', '/api/delete-profile')
        for path in paths:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)
                for method in ('POST', 'PUT', 'PATCH', 'DELETE'):
                    response = self.client.open(path, method=method, json={'password': 'legacy', 'clientSecret': 'replacement'})
                    self.assertEqual(response.status_code, 405)
                    self.assertEqual(response.headers['Allow'], 'GET, HEAD, OPTIONS')
        after = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(before, after)
        self.assertFalse(jobs.fetch_jobs)

    def test_registered_routes_are_read_only_even_with_old_admin_configuration(self):
        self.app.config.update(ADMIN_PASSWORD='old-password', SECRET_KEY='old-session-key')
        for rule in self.app.url_map.iter_rules():
            self.assertLessEqual(rule.methods, {'GET', 'HEAD', 'OPTIONS'}, rule.rule)
        for method in ('POST', 'PUT', 'PATCH', 'DELETE'):
            self.assertEqual(self.client.open('/api/public/v1/me', method=method).status_code, 405)
        self.assertEqual(self.client.get('/api/dashboard').status_code, 200)

    def test_publication_requires_explicit_opt_in(self):
        for mode in ('', 'private', 'typo'):
            with self.subTest(mode=mode), self.assertRaisesRegex(RuntimeError, 'FITFLARE_DATA_ACCESS=public'):
                create_app({**self.config, 'DATA_ACCESS_MODE': mode})
        from backend.config import settings
        with patch.dict(os.environ, {'FITFLARE_PROFILE_ID': 'me', 'FITFLARE_DATA_ACCESS': ''}):
            self.assertEqual(settings()['OWNER_PROFILE_ID'], 'me')
            self.assertEqual(settings()['DATA_ACCESS_MODE'], '')
            self.assertNotIn('SECRET_KEY', settings())
            with patch.dict(os.environ, {'FITBIT_PROFILE': 'Hidden'}):
                self.assertEqual(profile_paths.resolve_or_prompt_profile(), 'me')
            with self.assertRaises(ValueError):
                profile_paths.get_active_profile('../Hidden')

    def test_public_cors_and_no_session_cookie(self):
        for method in ('GET', 'HEAD', 'OPTIONS'):
            response = self.client.open('/api/public/v1/me', method=method)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers['Access-Control-Allow-Origin'], '*')
            self.assertEqual(response.headers['Access-Control-Allow-Methods'], 'GET, HEAD, OPTIONS')
            self.assertNotIn('Set-Cookie', response.headers)
        self.assertNotIn('Access-Control-Allow-Origin', self.client.get('/api/admin/session').headers)

    def test_static_credentials_and_raw_csv_are_not_web_accessible(self):
        for path in ('/.env', '/.git/config', '/server.py', '/backend/security.py', '/profiles/index.json',
                     '/profiles/Demo/csv/example.csv', '/profiles/Demo/auth/google_client.json',
                     '/profiles/Demo/auth/tokens.json', '/templates/public_api.html', '/static/server.py'):
            self.assertEqual(self.client.get(path).status_code, 404, path)

    def test_profile_traversal_and_symlinks_are_rejected(self):
        (self.root / 'profiles/Link').symlink_to(self.root, target_is_directory=True)
        for path in ('/api/dashboard/%2e%2e', '/api/dashboard/Link', '/api/tables/Hidden/sleep'):
            self.assertEqual(self.client.get(path).status_code, 404)
        self.assertEqual(self.client.get('/api/public/v1/me?profile=Hidden').status_code, 400)

    def test_account_only_reports_data_availability_without_reading_credentials(self):
        path = self.root / 'profiles/Demo/auth/tokens.json'
        path.write_text('unreadable as JSON: synthetic-token')
        self.assertEqual(self.client.get('/api/account').json, {'profile_id': 'Demo', 'has_data': True})
        (self.root / 'profiles/Demo/auth/google_client.json').write_text('unreadable synthetic credentials')
        self.assertEqual(self.client.get('/api/profiles').json, [{'name': 'Demo', 'created': 'Unknown'}])
        self.app.config['OWNER_PROFILE_ID'] = 'NewOwner'
        self.assertEqual(self.client.get('/api/account').json, {'profile_id': 'NewOwner', 'has_data': False})
        self.assertFalse((self.root / 'profiles/NewOwner').exists())

    def test_personal_aliases_keep_legacy_read_responses(self):
        for personal, legacy in (('/api/dashboard', '/api/dashboard/Demo'),
                                 ('/api/tables/sleep', '/api/tables/Demo/sleep'),
                                 ('/api/public/v1/me', '/api/public/v1/profiles/Demo'),
                                 ('/api/public/v1/me/dashboard', '/api/public/v1/profiles/Demo/dashboard')):
            self.assertEqual(self.client.get(personal).json, self.client.get(legacy).json)

    def test_background_jobs_keep_owner_isolation_without_web_controls(self):
        first, second = jobs.create_fetch('Demo'), jobs.create_fetch('Demo')
        self.assertNotEqual(first, second)
        jobs.fetch_jobs['legacy'] = dict(id='legacy', profile='Hidden', status='running')
        self.assertEqual(self.client.get('/api/health').json['active_jobs'], 0)
        self.assertEqual(self.client.get('/api/fetch-jobs').status_code, 404)
        self.assertEqual(jobs.fetch_jobs['legacy']['status'], 'running')
        with self.assertRaises(ValueError):
            jobs.create_fetch('Hidden')


if __name__ == '__main__':
    unittest.main()
