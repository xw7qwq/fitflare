"""Offline tests: every mutable path is redirected to a temporary synthetic fixture."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ['FITBAUS_AUTO_SYNC_ENABLED'] = 'false'
with patch.dict(os.environ, {'FITBAUS_ADMIN_PASSWORD': 'test-import-only',
                             'FITBAUS_ADMIN_PASSWORD_HASH': '', 'FITBAUS_SESSION_SECRET_FILE': ''}):
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
        self.config = dict(OWNER_PROFILE_ID='Demo', TESTING=True, SECRET_KEY='test-only-session-key-not-for-production',
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
        for path in ('/api/account', '/api/dashboard', '/api/tables/sleep', '/api/public/v1/me', '/api/profiles', '/api/profile-summaries', '/api/dashboard/Demo', '/api/tables/Demo/sleep', '/api/public/v1', '/api/public/v1/profiles/Demo/snapshot', '/api/public/v1/profiles/Demo/charts/overview-trend.svg'):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 401)
                self.assertNotIn('Access-Control-Allow-Origin', response.headers)
                self.assertIn('no-store', response.headers['Cache-Control'])
        with self.client.get('/') as response:
            self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get('/api/health').status_code, 200)
        self.login()
        self.assertEqual(self.client.get('/api/dashboard/Hidden').status_code, 404)
        self.assertEqual(self.client.get('/api/dashboard').status_code, 200)

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
        self.assertEqual(self.client.post('/api/account/setup', json={}).status_code, 401)
        self.assertEqual(self.client.post('/api/create-profile', json={}).status_code, 404)

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

    def test_jobs_survive_polling_and_ids_are_unique(self):
        first, second = jobs.create_fetch('Demo'), jobs.create_fetch('Demo')
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


    def test_default_is_private_and_owner_is_explicit(self):
        from backend.config import settings
        with patch.dict(os.environ, {'FITFLARE_PROFILE_ID': 'me'}):
            os.environ.pop('FITBAUS_DATA_ACCESS', None)
            self.assertEqual(settings()['DATA_ACCESS_MODE'], 'private')
            self.assertEqual(settings()['OWNER_PROFILE_ID'], 'me')
            self.assertEqual(profile_paths.get_active_profile(), 'me')
            with patch.dict(os.environ, {'FITBIT_PROFILE': 'Hidden'}):
                self.assertEqual(profile_paths.resolve_or_prompt_profile(), 'me')
            self.assertEqual(profile_paths.get_active_profile('Demo'), 'Demo')
            with self.assertRaises(ValueError):
                profile_paths.get_active_profile('../Hidden')

    def test_owner_restriction_survives_admin_login_and_public_wildcard(self):
        self.app.config['PUBLIC_PROFILE_IDS'] = None
        token = self.login()
        for prefix in ('/api/dashboard/Hidden', '/api/tables/Hidden/sleep',
                       '/api/public/v1/profiles/Hidden', '/api/public/v1/profiles/Hidden/dashboard',
                       '/api/authorize/Hidden', '/profiles/Hidden/csv/example.csv'):
            self.assertEqual(self.client.get(prefix).status_code, 404, prefix)
        headers = {'X-FitBaus-CSRF': token}
        for endpoint, body in (('/api/fetch-data', {'profile': 'Hidden'}),
                               ('/api/authorize-exchange', {'profileName': 'Hidden', 'code': 'synthetic'}),
                               ('/api/account/setup', {'profileName': 'Hidden', 'clientId': 'id', 'clientSecret': 'secret'})):
            self.assertEqual(self.client.post(endpoint, json=body, headers=headers).status_code, 400)
        self.assertEqual(self.client.post('/api/rebuild-dashboard/Hidden', json={}, headers=headers).status_code, 404)
        for endpoint in ('create-profile', 'delete-profile'):
            self.assertEqual(self.client.post('/api/'+endpoint, json={'profileName':'Demo'}, headers=headers).status_code, 404)
        self.assertEqual([p['name'] for p in self.client.get('/api/profiles').json], ['Demo'])

    def test_account_bootstrap_and_credential_update_preserve_data_and_tokens(self):
        self.app.config['OWNER_PROFILE_ID'] = 'NewOwner'
        before = self.client.get('/api/account').json
        self.assertEqual(before, dict(profile_id='NewOwner', configured=False, authorized=False, has_data=False))
        credentials = {'clientId': 'synthetic-id', 'clientSecret': 'synthetic-secret'}
        self.assertEqual(self.client.post('/api/account/setup', json=credentials).status_code, 401)
        token = self.login()
        self.assertEqual(self.client.post('/api/account/setup', json=credentials).status_code, 403)
        headers = {'X-FitBaus-CSRF': token}
        response = self.client.post('/api/account/setup', json=credentials, headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json['configured'])
        self.assertFalse(response.json['authorized'])
        directory = self.root / 'profiles/NewOwner'
        path = directory / 'auth/client.json'
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        saved = json.loads(path.read_text())
        self.assertEqual(saved['client_id'], 'synthetic-id')
        token_path = directory / 'auth/tokens.json'
        token_path.write_text(json.dumps({'refresh_token': 'synthetic-refresh', 'access_token': 'synthetic-access'}))
        health = directory / 'csv/health.csv'
        health.write_text('date,value\n2026-01-01,5\n')
        original = token_path.read_bytes(), health.read_bytes()
        credentials['clientSecret'] = 'replacement-secret'
        updated = self.client.post('/api/account/setup', json=credentials, headers=headers)
        self.assertEqual(updated.status_code, 200)
        self.assertTrue(updated.json['authorized'])
        self.assertTrue(updated.json['has_data'])
        self.assertEqual((token_path.read_bytes(), health.read_bytes()), original)
        self.assertEqual(json.loads(path.read_text())['created_at'], saved['created_at'])
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        public = self.client.get('/api/account').get_data(as_text=True)
        for secret in ('synthetic-refresh', 'synthetic-access', 'replacement-secret', 'synthetic-id'):
            self.assertNotIn(secret, public)
        self.assertFalse(list((directory / 'auth').glob('.client-*.tmp')))

    def test_environment_credentials_and_existing_cache_without_authorization(self):
        self.assertEqual(self.client.get('/api/account').json,
                         dict(profile_id='Demo', configured=False, authorized=False, has_data=True))
        self.assertEqual(self.client.get('/api/dashboard').status_code, 200)
        with patch.dict(os.environ, {'FITBIT_CLIENT_ID': 'environment-id', 'FITBIT_CLIENT_SECRET': 'environment-secret'}):
            self.assertTrue(self.client.get('/api/account').json['configured'])
            self.login()
            response = self.client.get('/api/authorize')
            self.assertEqual(response.status_code, 200)
            self.assertIn('client_id=environment-id', response.json['auth_url'])
            self.assertNotIn('environment-secret', response.get_data(as_text=True))

    def test_personal_aliases_keep_legacy_responses(self):
        for personal, legacy in (('/api/dashboard', '/api/dashboard/Demo'),
                                 ('/api/tables/sleep', '/api/tables/Demo/sleep'),
                                 ('/api/public/v1/me', '/api/public/v1/profiles/Demo'),
                                 ('/api/public/v1/me/dashboard', '/api/public/v1/profiles/Demo/dashboard')):
            self.assertEqual(self.client.get(personal).json, self.client.get(legacy).json)
        self.assertEqual(self.client.get('/api/public/v1/me?profile=Hidden').status_code, 400)
        token = self.login()
        with patch('backend.admin_routes.threading.Thread') as thread:
            result = self.client.post('/api/fetch-data', json={}, headers={'X-FitBaus-CSRF':token})
            self.assertEqual(result.status_code, 200)
            self.assertEqual(jobs.fetch_jobs[result.json['job_id']]['profile'], 'Demo')
            thread.return_value.start.assert_called_once()

    def test_legacy_jobs_are_not_readable_or_mutable(self):
        jobs.fetch_jobs['legacy'] = dict(id='legacy', profile='Hidden', status='running')
        jobs.auth_jobs['legacy'] = dict(id='legacy', profile='Hidden', status='running')
        token = self.login()
        self.assertEqual(self.client.get('/api/fetch-jobs').json, [])
        self.assertEqual(self.client.get('/api/fetch-status/legacy').status_code, 404)
        self.assertEqual(self.client.get('/api/authorize-status/legacy').status_code, 404)
        self.assertEqual(self.client.post('/api/cancel-fetch/legacy', json={}, headers={'X-FitBaus-CSRF':token}).status_code, 404)
        self.assertEqual(jobs.fetch_jobs['legacy']['status'], 'running')
        self.assertEqual(self.client.get('/api/health').json['active_jobs'], 0)
        with self.assertRaises(ValueError):
            jobs.create_fetch('Hidden')

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

    def test_inner_symlinks_cannot_read_another_account(self):
        self.login()
        linked_csv = self.root / 'profiles/Demo/csv/linked.csv'
        linked_csv.symlink_to(self.root / 'profiles/Hidden/csv/example.csv')
        self.assertEqual(self.client.get('/profiles/Demo/csv/linked.csv').status_code, 404)
        cache = self.root / 'profiles/Demo/cache/dashboard.json'
        cache.unlink()
        cache.symlink_to(self.root / 'profiles/Hidden/cache/dashboard.json')
        response = self.client.get('/api/dashboard')
        self.assertEqual(response.status_code, 500)
        self.assertNotIn('Hidden', response.get_data(as_text=True))



    def test_setup_failed_atomic_replace_preserves_previous_credentials(self):
        path = self.root / 'profiles/Demo/auth/client.json'
        previous = path.read_bytes()
        token = self.login()
        with patch('backend.admin_routes.os.replace', side_effect=OSError('Synthetic failure')):
            response = self.client.post('/api/account/setup', json={'clientId':'new', 'clientSecret':'new-secret'},
                                        headers={'X-FitBaus-CSRF':token})
        self.assertEqual(response.status_code, 500)
        self.assertEqual(path.read_bytes(), previous)
        self.assertFalse(list(path.parent.glob('.client-*.tmp')))

    def test_authorization_exchange_and_rebuild_alias_only_use_owner(self):
        token = self.login()
        headers = {'X-FitBaus-CSRF':token}
        with patch.dict(os.environ, {'FITBIT_CLIENT_ID':'synthetic-id', 'FITBIT_CLIENT_SECRET':'synthetic-secret'}), \
             patch('auth.authorize_fitbit.exchange_code_for_token', return_value=True) as exchange:
            result = self.client.post('/api/authorize-exchange', json={'code':'synthetic-code'}, headers=headers)
            self.assertEqual(result.status_code, 200)
            self.assertEqual(exchange.call_args.kwargs['profile_id'], 'Demo')
            invalid = self.client.post('/api/authorize-exchange', json={'profile':'Hidden', 'code':'synthetic-code'}, headers=headers)
            self.assertEqual(invalid.status_code, 400)
            exchange.assert_called_once()
        with patch('backend.admin_routes.build_dashboard_cache', return_value={'generated_at':'synthetic'}) as rebuild:
            self.assertEqual(self.client.post('/api/rebuild-dashboard', json={}, headers=headers).status_code, 200)
            rebuild.assert_called_once_with('Demo')



    def test_environment_managed_setup_rejects_without_writing(self):
        path = self.root / 'profiles/Demo/auth/client.json'
        previous = path.read_bytes()
        token = self.login()
        with patch.dict(os.environ, {'FITBIT_CLIENT_ID':'env-id', 'FITBIT_CLIENT_SECRET':'env-secret'}):
            response = self.client.post('/api/account/setup', json={'clientId':'new', 'clientSecret':'new-secret'},
                                        headers={'X-FitBaus-CSRF':token})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json['code'], 'credentials_managed_by_environment')
        self.assertEqual(path.read_bytes(), previous)

    def test_explicit_cli_maintenance_uses_matching_token_and_credentials(self):
        from auth.refresh_token import _resolve_tokens_file, _resolve_client_credentials
        from common.fitbit_profile import _ensure_env_for_profile
        for account in ('Demo', 'Hidden'):
            path = self.root / f'profiles/{account}/auth/client.json'
            path.write_text(json.dumps({'client_id': account+'-id', 'client_secret':account+'-secret'}))
        self.assertEqual(profile_paths.owner_profile_id(), 'Demo')
        _ensure_env_for_profile('Hidden')
        self.assertEqual(Path(_resolve_tokens_file()), (self.root / 'profiles/Hidden/auth/tokens.json').resolve())
        self.assertEqual(_resolve_client_credentials(), ('Hidden-id', 'Hidden-secret'))
        self.assertEqual(profile_paths.get_active_profile(), 'Hidden')


if __name__ == '__main__':
    unittest.main()
