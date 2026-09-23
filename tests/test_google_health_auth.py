"""Offline Google OAuth regression tests; only synthetic temporary credentials."""
import base64
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlencode, urlsplit

from common import google_health_auth as oauth, profile_paths


def response(status, payload):
    result = Mock(status_code=status)
    result.json.return_value = payload
    return result


class GoogleHealthAuthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        patcher = patch.object(profile_paths, "ROOT_DIR", str(self.root))
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.dict(os.environ, {}, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.path = self.root / "profiles" / "Demo" / "auth" / "tokens.json"
        self.path.parent.mkdir(parents=True)
        self.legacy = {"access_token": "old-test-access", "refresh_token": "old-test-refresh", "user_id": "ABC123"}
        oauth._write_json(self.path, self.legacy)
        oauth._write_json(self.path.with_name("client.json"), {"client_id": "fitbit-app", "client_secret": "fitbit-secret"})
        oauth.save_google_credentials("Demo", "example.apps.googleusercontent.com", "google-secret")
        self.http = Mock()
        self.token_response = {"access_token": "google-test-access", "refresh_token": "google-test-refresh",
                               "scope": oauth.GOOGLE_HEALTH_SCOPE_TEXT, "expires_in": 3600}
        self.http.post.side_effect = [response(200, self.token_response), response(200, {})]
        self.http.get.return_value = response(200, {"healthUserId": "health-user", "legacyUserId": "ABC123"})

    def begin(self, binding="test-session"):
        flow = oauth.begin_google_authorization("Demo", session_binding=binding)
        params = parse_qs(urlsplit(flow["auth_url"]).query)
        return flow, flow["redirect_uri"] + "?" + urlencode({"code": "test-code", "state": params["state"][0]})

    def complete(self, url, binding="test-session"):
        return oauth.complete_google_authorization("Demo", url, session_binding=binding, session=self.http)

    def assert_legacy_untouched(self):
        self.assertEqual(oauth._read_json(self.path), self.legacy)
        self.assertFalse(self.path.with_name("fitbit_revocation_pending.json").exists())

    def test_credentials_do_not_replace_legacy_or_fall_back_to_fitbit_client(self):
        self.assert_legacy_untouched()
        self.path.with_name("google_client.json").unlink()
        with self.assertRaises(oauth.GoogleHealthAuthError) as error:
            oauth.resolve_google_credentials("Demo")
        self.assertEqual(error.exception.code, "google_credentials_missing")
        self.assertTrue(self.path.with_name("client.json").exists())

    def test_environment_and_downloaded_google_credentials(self):
        oauth._write_json(self.path.with_name("google_client.json"), {"web": {
            "client_id": "downloaded.apps.googleusercontent.com", "client_secret": "downloaded-secret",
            "redirect_uris": ["https://example.test/oauth/callback"]}})
        self.assertEqual(oauth.resolve_google_credentials("Demo")["redirect_uri"], "https://example.test/oauth/callback")
        with patch.dict(os.environ, {"GOOGLE_HEALTH_CLIENT_ID": "env.apps.googleusercontent.com",
                                     "GOOGLE_HEALTH_CLIENT_SECRET": "env-secret", "GOOGLE_HEALTH_REDIRECT_URI": ""}):
            self.assertEqual(oauth.resolve_google_credentials("Demo")["client_id"], "env.apps.googleusercontent.com")
            self.assertEqual(oauth.resolve_google_credentials("Demo")["redirect_uri"], "https://example.test/oauth/callback")

    def test_pkce_offline_and_state_are_secure(self):
        flow, _ = self.begin()
        params = parse_qs(urlsplit(flow["auth_url"]).query)
        pending = oauth._read_json(self.path.with_name("google_oauth_pending.json"))
        challenge = base64.urlsafe_b64encode(hashlib.sha256(pending["code_verifier"].encode()).digest()).decode().rstrip("=")
        self.assertEqual(params["code_challenge"], [challenge])
        self.assertEqual(params["code_challenge_method"], ["S256"])
        self.assertEqual(params["access_type"], ["offline"])
        self.assertEqual(params["prompt"], ["consent"])
        self.assertEqual(set(params["scope"][0].split()), set(oauth.GOOGLE_HEALTH_SCOPES))
        for secret in ("google-secret", pending["code_verifier"], "old-test-access", "old-test-refresh"):
            self.assertNotIn(secret, json.dumps(flow))
        self.assertEqual(self.path.with_name("google_oauth_pending.json").stat().st_mode & 0o777, 0o600)

    def test_state_redirect_session_and_missing_full_url_fail_before_network(self):
        _, url = self.begin()
        for bad_url, binding in ((url.replace("state=", "state=wrong"), "test-session"),
                                 (url.replace("localhost", "attacker.test"), "test-session"),
                                 (url, "different-session"), ("test-code", "test-session"),
                                 (url + "&state=duplicate", "test-session")):
            with self.subTest(url=bad_url, binding=binding), self.assertRaises(oauth.GoogleHealthAuthError):
                self.complete(bad_url, binding)
        self.http.post.assert_not_called()
        self.assert_legacy_untouched()

    def test_expired_state_and_replay_are_rejected(self):
        _, url = self.begin()
        with patch.object(oauth.time, "time", return_value=10**12):
            with self.assertRaises(oauth.GoogleHealthAuthError) as error:
                self.complete(url)
            self.assertEqual(error.exception.code, "oauth_state_expired")
        self.complete(url)
        calls = self.http.post.call_count
        with self.assertRaises(oauth.GoogleHealthAuthError):
            self.complete(url)
        self.assertEqual(self.http.post.call_count, calls)

    def test_valid_identity_atomically_switches_before_immediate_revocation(self):
        _, url = self.begin()
        def post(url, **kwargs):
            if url == oauth.FITBIT_REVOKE_URL:
                self.assertEqual(oauth._read_json(self.path)["oauth_type"], "google")
                self.assertEqual(kwargs["data"], {"token": "old-test-refresh"})
                self.assertEqual(kwargs["auth"], ("fitbit-app", "fitbit-secret"))
                return response(200, {})
            self.assertEqual(url, oauth.TOKEN_URL)
            self.assertIn("code_verifier", kwargs["data"])
            return response(200, self.token_response)
        self.http.post.side_effect = post
        result = self.complete(url)
        saved = oauth._read_json(self.path)
        self.assertEqual(saved["oauth_type"], "google")
        self.assertEqual(saved["health_user_id"], "health-user")
        self.assertEqual(saved["legacy_user_id"], "ABC123")
        self.assertEqual(saved["granted_scopes"], sorted(oauth.GOOGLE_HEALTH_SCOPES))
        self.assertNotIn("old-test-refresh", json.dumps(saved))
        self.assertNotIn("access_token", result)
        self.assertFalse(result["legacy_revocation_pending"])
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.http.get.call_args.args[0], oauth.IDENTITY_URL)

    def test_account_not_linked_preserves_legacy_connection(self):
        _, url = self.begin()
        self.http.get.return_value = response(400, {"error": {"details": [{"reason": "ACCOUNT_NOT_LINKED"}]}})
        with self.assertRaises(oauth.GoogleHealthAuthError) as error:
            self.complete(url)
        self.assertEqual(error.exception.code, "account_not_linked")
        self.assertEqual(error.exception.details["redirect_uri"], oauth.SIGNUP_URL)
        self.assertEqual(self.http.post.call_count, 1)
        self.assert_legacy_untouched()

    def test_mismatched_account_cannot_mix_histories(self):
        for identity in ({"healthUserId": "other", "legacyUserId": "OTHER1"}, {"healthUserId": "other"}):
            with self.subTest(identity=identity):
                _, url = self.begin()
                self.http.post.side_effect = [response(200, self.token_response)]
                self.http.get.return_value = response(200, identity)
                with self.assertRaises(oauth.GoogleHealthAuthError) as error:
                    self.complete(url)
                self.assertEqual(error.exception.code, "account_mismatch")
                self.assert_legacy_untouched()

    def test_zero_scope_preserves_legacy_and_partial_scope_has_actionable_warning(self):
        _, url = self.begin()
        self.http.post.side_effect = [response(200, {**self.token_response, "scope": "openid email"})]
        with self.assertRaises(oauth.GoogleHealthAuthError) as error:
            self.complete(url)
        self.assertEqual(error.exception.code, "missing_permissions")
        self.assert_legacy_untouched()
        self.http.get.assert_not_called()
        _, url = self.begin()
        self.http.post.side_effect = [response(200, {**self.token_response, "scope": oauth.GOOGLE_HEALTH_SCOPES[0]}), response(200, {})]
        result = self.complete(url)
        self.assertTrue(result["warning"])
        self.assertEqual(len(result["missing_scopes"]), len(oauth.GOOGLE_HEALTH_SCOPES) - 1)
        self.assertEqual(result["granted_scopes"], [oauth.GOOGLE_HEALTH_SCOPES[0]])

    def test_sync_lock_prevents_switch_and_consumption_of_state(self):
        _, url = self.begin()
        with oauth.profile_migration_lock(self.path):
            with self.assertRaises(oauth.GoogleHealthAuthError) as error:
                self.complete(url)
            self.assertEqual(error.exception.status, 409)
        self.assertTrue(self.path.with_name("google_oauth_pending.json").exists())
        self.http.post.assert_not_called()
        self.assert_legacy_untouched()

    def test_revocation_failure_is_visible_and_retried_on_refresh(self):
        _, url = self.begin()
        self.http.post.side_effect = [response(200, self.token_response), response(503, {})]
        result = self.complete(url)
        self.assertTrue(result["legacy_revocation_pending"])
        self.assertTrue(result["warning"])
        self.assertEqual(oauth._read_json(self.path)["oauth_type"], "google")
        self.http.post.side_effect = [response(200, {"access_token": "refreshed-google-access", "expires_in": 3600}), response(200, {})]
        token = oauth.refresh_google_token("Demo", session=self.http)
        self.assertEqual(token, "refreshed-google-access")
        self.assertFalse(self.path.with_name("fitbit_revocation_pending.json").exists())
        saved = oauth._read_json(self.path)
        self.assertFalse(saved["legacy_revocation_pending"])
        self.assertEqual(saved["health_user_id"], "health-user")
        self.assertEqual(saved["refresh_token"], "google-test-refresh")
        self.assertEqual(saved["scope"], " ".join(sorted(oauth.GOOGLE_HEALTH_SCOPES)))

    def test_new_user_and_reauthorization_require_stable_identity(self):
        oauth._write_json(self.path, {})
        _, url = self.begin()
        self.http.get.return_value = response(200, {"healthUserId": "health-user"})
        result = self.complete(url)
        self.assertEqual(result["oauth_type"], "google")
        self.assertEqual(self.http.post.call_count, 1)
        _, url = self.begin()
        self.http.post.side_effect = [response(200, self.token_response)]
        self.http.get.return_value = response(200, {"healthUserId": "different-health-user"})
        with self.assertRaises(oauth.GoogleHealthAuthError) as error:
            self.complete(url)
        self.assertEqual(error.exception.code, "account_mismatch")
        self.assertEqual(oauth._read_json(self.path)["health_user_id"], "health-user")

    def test_missing_refresh_token_does_not_cut_over(self):
        _, url = self.begin()
        token = {key: value for key, value in self.token_response.items() if key != "refresh_token"}
        self.http.post.side_effect = [response(200, token)]
        with self.assertRaises(oauth.GoogleHealthAuthError) as error:
            self.complete(url)
        self.assertEqual(error.exception.code, "refresh_token_missing")
        self.assert_legacy_untouched()

    def test_refresh_dispatch_never_sends_google_token_to_fitbit(self):
        from auth import refresh_token as refresh
        oauth._write_json(self.path, {"oauth_type": "google", "refresh_token": "google-refresh"})
        with patch.dict(os.environ, {"FITFLARE_PROFILE_ID": "Demo"}), patch.object(oauth, "refresh_google_token", return_value="new-google") as google, patch.object(refresh, "requests") as legacy_http:
            self.assertEqual(refresh.refresh_token(), "new-google")
        google.assert_called_once_with("Demo", tokens_path=str(self.path), _lock_held=True)
        legacy_http.post.assert_not_called()

    def test_client_change_preserves_refresh_for_active_client_until_new_consent(self):
        _, url = self.begin()
        self.complete(url)
        oauth.save_google_credentials("Demo", "replacement.apps.googleusercontent.com", "replacement-secret")
        self.http.post.side_effect = [response(200, {"access_token": "refreshed-google"})]
        oauth.refresh_google_token("Demo", session=self.http)
        self.assertEqual(self.http.post.call_args.kwargs["data"]["client_id"], "example.apps.googleusercontent.com")
        self.assertEqual(self.http.post.call_args.kwargs["data"]["client_secret"], "google-secret")
        _, url = self.begin()
        self.http.post.side_effect = [response(200, {"access_token": "replacement-access", "scope": oauth.GOOGLE_HEALTH_SCOPE_TEXT})]
        with self.assertRaises(oauth.GoogleHealthAuthError) as error:
            self.complete(url)
        self.assertEqual(error.exception.code, "refresh_token_missing")
        self.assertEqual(oauth._read_json(self.path)["oauth_client_id"], "example.apps.googleusercontent.com")

    def test_direct_refresh_and_migration_share_token_lock(self):
        from auth import refresh_token as refresh
        _, url = self.begin()
        with oauth.profile_token_lock(self.path):
            with self.assertRaises(oauth.GoogleHealthAuthError) as error:
                self.complete(url)
            self.assertEqual(error.exception.code, "oauth_in_progress")
            with patch.dict(os.environ, {"FITFLARE_PROFILE_ID": "Demo"}):
                with self.assertRaises(oauth.GoogleHealthAuthError):
                    refresh.refresh_token()
        self.http.post.assert_not_called()
        self.assert_legacy_untouched()

    def test_old_fitbit_callback_cannot_overwrite_completed_google_migration(self):
        from auth import authorize_fitbit as legacy_auth
        _, url = self.begin()
        self.complete(url)
        saved = self.path.read_bytes()
        with patch.object(legacy_auth.requests, "post") as http, patch.object(legacy_auth, "print") as output:
            self.assertFalse(legacy_auth.exchange_code_for_token("old-tab-code", "http://localhost:8080/callback", "old-client", "old-secret", "Demo"))
            http.assert_not_called()
            self.assertIn("authorize_google_health.py", str(output.call_args))
        self.assertEqual(self.path.read_bytes(), saved)

    def test_legacy_authorization_checks_before_prompt_and_obeys_both_locks(self):
        from auth import authorize_fitbit as legacy_auth
        with patch.object(legacy_auth.requests, "post") as http, patch.object(legacy_auth, "print"):
            for lock in (oauth.profile_migration_lock, oauth.profile_token_lock):
                with lock(self.path):
                    self.assertFalse(legacy_auth.exchange_code_for_token("old-code", "http://localhost:8080/callback", "old-client", "old-secret", "Demo"))
            http.assert_not_called()
        self.assert_legacy_untouched()
        oauth._write_json(self.path, {"oauth_type": "google", "access_token": "google-access"})
        with patch("sys.argv", ["authorize_fitbit.py", "--profile", "Demo"]), patch.object(legacy_auth, "_load_or_prompt_credentials") as prompt, patch.object(legacy_auth, "get_auth_code_advanced") as browser, patch.object(legacy_auth, "print"):
            legacy_auth.main()
            prompt.assert_not_called()
            browser.assert_not_called()


class GoogleHealthAuthRouteTests(unittest.TestCase):
    def setUp(self):
        with patch.dict(os.environ, {"FITBAUS_ADMIN_PASSWORD": "test-import-only", "FITBAUS_ADMIN_PASSWORD_HASH": "",
                                     "FITBAUS_SESSION_SECRET_FILE": "", "FITBAUS_AUTO_SYNC_ENABLED": "false"}):
            from server import create_app
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "profiles" / "Demo" / "auth" / "tokens.json"
        self.path.parent.mkdir(parents=True)
        self.legacy = {"access_token": "old-access", "refresh_token": "old-refresh", "user_id": "ABC123"}
        oauth._write_json(self.path, self.legacy)
        old_cwd = os.getcwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, old_cwd)
        patcher = patch.object(profile_paths, "ROOT_DIR", str(self.root))
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.dict(os.environ, {"FITFLARE_PROFILE_ID": "Demo"}, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.create_app = create_app
        self.app = self.make_app("Demo")
        self.client = self.app.test_client()

    def make_app(self, owner):
        return self.create_app(dict(TESTING=True, SECRET_KEY="test-only-key", ADMIN_PASSWORD="test-password",
                                    ADMIN_PASSWORD_HASH="", SESSION_COOKIE_SECURE=False, OWNER_PROFILE_ID=owner,
                                    PROFILES_DIR=str(self.root / "profiles"), DATA_ACCESS_MODE="private"))

    def login(self, client=None):
        response = (client or self.client).post("/api/admin/login", json={"password": "test-password"})
        return {"X-FitBaus-CSRF": response.json["csrf_token"]}

    def credentials(self):
        return {"clientId": "test.apps.googleusercontent.com",
                "clientSecret": "google-test-secret", "redirectUri": "http://localhost:8080/callback"}

    def test_credentials_endpoint_requires_admin_csrf_and_preserves_active_tokens(self):
        endpoint = "/api/account/setup"
        self.assertEqual(self.client.post(endpoint, json=self.credentials()).status_code, 401)
        headers = self.login()
        self.assertEqual(self.client.post(endpoint, json=self.credentials()).status_code, 403)
        result = self.client.get("/api/authorize")
        self.assertEqual(result.status_code, 400)
        self.assertEqual(result.json["code"], "google_credentials_missing")
        result = self.client.post(endpoint, json=self.credentials(), headers=headers)
        self.assertEqual(result.status_code, 200)
        self.assertNotIn("google-test-secret", result.get_data(as_text=True))
        self.assertEqual(oauth._read_json(self.path), self.legacy)
        self.assertEqual(self.client.get("/api/authorize").json["provider"], "google")

    def test_fresh_account_setup_saves_google_client_without_fake_connection(self):
        app = self.make_app("NewUser")
        client = app.test_client()
        headers = self.login(client)
        self.assertEqual(client.post("/api/create-profile", json={"profileName": "Other"}, headers=headers).status_code, 404)
        response = client.post("/api/account/setup", json=self.credentials(), headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual((response.json["configured"], response.json["authorized"]), (True, False))
        auth_dir = self.root / "profiles" / "NewUser" / "auth"
        self.assertTrue((auth_dir / "google_client.json").exists())
        self.assertFalse((auth_dir / "client.json").exists())
        self.assertFalse((auth_dir / "tokens.json").exists())
        status = client.get("/api/account")
        self.assertEqual((status.json["profile_id"], status.json["authorized"]), ("NewUser", False))
        self.assertNotIn("google-test-secret", status.get_data(as_text=True) + response.get_data(as_text=True))

    def test_cache_rebuild_failure_does_not_turn_completed_oauth_into_failure(self):
        headers = self.login()
        result = {"message": "Connected", "oauth_type": "google", "warning": "Existing permission warning."}
        with patch("backend.admin_routes.complete_google_authorization", return_value=result), patch("backend.admin_routes.build_dashboard_cache", side_effect=OSError("cache write failed")) as rebuild:
            response = self.client.post("/api/authorize-exchange", json={"redirectUrl": "http://localhost:8080/callback?code=test&state=test"}, headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["oauth_type"], "google")
        self.assertIn("Existing permission warning.", response.json["warning"])
        self.assertIn("重建缓存", response.json["warning"])
        rebuild.assert_called_once_with("Demo")

    def test_manual_oauth_is_bound_to_original_admin_session(self):
        headers = self.login()
        self.client.post("/api/account/setup", json=self.credentials(), headers=headers)
        flow = self.client.get("/api/authorize").json
        state = parse_qs(urlsplit(flow["auth_url"]).query)["state"][0]
        full_url = flow["redirect_uri"] + "?" + urlencode({"code": "test-code", "state": state})
        other = self.app.test_client()
        other_headers = self.login(other)
        with patch.object(oauth.requests, "post") as http:
            result = other.post("/api/authorize-exchange", json={"redirectUrl": full_url}, headers=other_headers)
            self.assertEqual(result.status_code, 400)
            self.assertEqual(result.json["code"], "oauth_state_invalid")
            http.assert_not_called()
        self.assertEqual(oauth._read_json(self.path), self.legacy)


if __name__ == "__main__":
    unittest.main()
