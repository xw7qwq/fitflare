"""Offline tests: every mutable path is redirected to a temporary synthetic fixture."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ['FITBAUS_AUTO_SYNC_ENABLED'] = 'false'
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
        self.config = dict(TESTING=True, SECRET_KEY='test-only-session-key-not-for-production',
                           ADMIN_PASSWORD='test-only-password', ADMIN_PASSWORD_HASH='',
                           SESSION_COOKIE_SECURE=False, PROFILES_DIR=str(self.root / 'profiles'),
                           PUBLIC_PROFILE_IDS=frozenset({'Demo'}), DATA_ACCESS_MODE='public')
        self.app = create_app(self.config)
        self.client = self.app.test_client()
        jobs.fetch_jobs.clear()
        jobs.auth_jobs.clear()

    def login(self, client=None):
        client = client or self.client
        response = client.post('/api/admin/login', json={'password': 'test-only-password'})
        self.assertEqual(response.status_code, 200)
        return response.json['csrf_token']

    def test_public_dashboard_is_filtered_on_server(self):
        response = self.client.get('/api/dashboard/Demo')
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('files', response.json)
        self.assertNotIn('/private/', response.get_data(as_text=True))
        self.login()
        self.assertIn('files', self.client.get('/api/dashboard/Demo').json)

    def test_all_read_surfaces_honor_profile_allowlist(self):
        for suffix in ('', '/dashboard', '/catalog', '/overview', '/coverage', '/metrics', '/correlations', '/series/daily', '/datasets', '/sections', '/tables', '/snapshot', '/snapshot/endpoints', '/charts/overview-trend.svg'):
            with self.subTest(suffix=suffix):
                self.assertEqual(self.client.get('/api/public/v1/profiles/Hidden'+suffix).status_code, 404)
        self.assertEqual(self.client.get('/api/dashboard/Hidden').status_code, 404)
        self.assertEqual(self.client.get('/api/tables/Hidden/sleep').status_code, 404)
        self.assertEqual([p['name'] for p in self.client.get('/api/profiles').json], ['Demo'])
        self.assertEqual([p['id'] for p in self.client.get('/api/profile-summaries').json], ['Demo'])
        self.assertEqual(self.client.get('/api/public/v1/profiles').json['meta']['count'], 1)

    def test_empty_allowlist_does_not_fall_back_to_all_profiles(self):
        self.app.config['PUBLIC_PROFILE_IDS'] = frozenset()
        self.assertEqual(self.client.get('/api/profiles').json, [])
        self.assertEqual(self.client.get('/api/profile-summaries').json, [])
        self.assertNotIn('Hidden', self.client.get('/api/public/v1/docs').get_data(as_text=True))

    def test_private_mode_denies_every_data_entry_point(self):
        self.app.config['DATA_ACCESS_MODE'] = 'private'
        for path in ('/api/profiles', '/api/profile-summaries', '/api/dashboard/Demo', '/api/tables/Demo/sleep', '/api/public/v1', '/api/public/v1/profiles/Demo/snapshot', '/api/public/v1/profiles/Demo/charts/overview-trend.svg'):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 401)
                self.assertNotIn('Access-Control-Allow-Origin', response.headers)
                self.assertIn('no-store', response.headers['Cache-Control'])
        with self.client.get('/') as response:
            self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get('/api/health').status_code, 200)
        self.login()
        self.assertEqual(self.client.get('/api/dashboard/Hidden').status_code, 200)

    def test_private_mode_fails_closed_without_credentials(self):
        with self.assertRaises(RuntimeError):
            create_app({**self.config, 'DATA_ACCESS_MODE':'private', 'ADMIN_PASSWORD':''})
        with self.assertRaises(RuntimeError):
            create_app({**self.config, 'DATA_ACCESS_MODE':'typo'})

    def test_csrf_and_logout_invalidate_session(self):
        token = self.login()
        self.assertEqual(self.client.post('/api/admin/logout').status_code, 403)
        self.assertEqual(self.client.post('/api/admin/logout', headers={'X-FitBaus-CSRF':token}).status_code, 200)
        self.assertFalse(self.client.get('/api/admin/session').json['authenticated'])
        self.assertEqual(self.client.post('/api/create-profile', json={}).status_code, 401)

    def test_login_rate_limit_and_malformed_body(self):
        self.assertEqual(self.client.post('/api/admin/login', json=[]).status_code, 400)
        for _ in range(5):
            self.assertEqual(self.client.post('/api/admin/login', json={'password':'incorrect'}).status_code, 401)
        result = self.client.post('/api/admin/login', json={'password':'incorrect'})
        self.assertEqual(result.status_code, 429)
        self.assertIn('Retry-After', result.headers)

    def test_public_cors_does_not_extend_to_management(self):
        response = self.client.get('/api/public/v1/profiles')
        self.assertEqual(response.headers['Access-Control-Allow-Origin'], '*')
        self.assertNotIn('Access-Control-Allow-Origin', self.client.get('/api/admin/session').headers)
        self.login()
        self.assertNotIn('Access-Control-Allow-Origin', self.client.get('/api/public/v1/profiles').headers)

    def test_tables_are_optional_and_server_paginated(self):
        self.assertNotIn('tables', self.client.get('/api/dashboard/Demo?tables=none').json)
        response = self.client.get('/api/tables/Demo/sleep?offset=20&limit=20').json
        self.assertEqual(response['meta'], dict(total=55, offset=20, limit=20, count=20))
        self.assertEqual(len(response['rows']), 20)
        self.assertEqual(self.client.get('/api/tables/Demo/sleep?offset=100000').json['rows'], [])
        self.assertEqual(self.client.get('/api/tables/Demo/unknown').status_code, 404)
        self.assertEqual(self.client.get('/api/tables/Demo/sleep?limit=9999').json['meta']['limit'], 100)

    def test_static_and_csv_access_boundaries(self):
        for path in ('/.env', '/.git/config', '/server.py', '/backend/security.py', '/profiles/index.json', '/templates/public_api.html', '/static/server.py'):
            self.assertEqual(self.client.get(path).status_code, 404, path)
        self.assertEqual(self.client.get('/profiles/Demo/csv/example.csv').status_code, 401)
        self.login()
        with self.client.get('/profiles/Demo/csv/example.csv') as response:
            self.assertEqual(response.status_code, 200)

    def test_profile_traversal_and_symlinks_are_rejected(self):
        (self.root / 'profiles/Link').symlink_to(self.root, target_is_directory=True)
        for path in ('/api/dashboard/%2e%2e', '/api/dashboard/Link', '/api/authorize/%2e%2e'):
            self.assertEqual(self.client.get(path).status_code, 404)
        token = self.login()
        response = self.client.post('/api/authorize-exchange', json={'profileName':'../outside','code':'test-only'}, headers={'X-FitBaus-CSRF':token})
        self.assertEqual(response.status_code, 400)

    def test_docs_and_all_sample_public_routes_render(self):
        self.assertEqual(self.client.get('/api/public/v1/docs').status_code, 200)
        text = self.client.get('/api/public/v1/docs').get_data(as_text=True)
        self.assertIn('YOUR_PROFILE', text)
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

    def test_jobs_survive_polling_and_ids_are_unique(self):
        first, second = jobs.create_fetch('Demo'), jobs.create_fetch('Hidden')
        self.assertNotEqual(first, second)
        self.login()
        for _ in range(3):
            self.assertEqual(self.client.get('/api/fetch-status/'+first).json['profile'], 'Demo')
        self.assertEqual(self.client.get('/api/fetch-jobs').status_code, 200)

    def test_manual_job_start_uses_registry_and_prevents_duplicate(self):
        token = self.login()
        with patch('backend.admin_routes.threading.Thread') as thread:
            response = self.client.post('/api/fetch-data', json={'profile':'Demo'}, headers={'X-FitBaus-CSRF':token})
            self.assertEqual(response.status_code, 200)
            thread.return_value.start.assert_called_once()
            second = self.client.post('/api/fetch-data', json={'profile':'Demo'}, headers={'X-FitBaus-CSRF':token})
            self.assertEqual(second.status_code, 409)

    def test_no_scheduler_starts_on_import(self):
        self.assertIsNone(sync.auto_sync_thread)


if __name__ == '__main__':
    unittest.main()
