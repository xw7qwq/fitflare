"""Google Health OAuth and safe per-profile migration from Fitbit.

OAuth state and PKCE verifiers stay on disk, never in the Flask session cookie.
Only this module switches tokens.json to Google after validating account identity.
"""
import base64
from contextlib import contextmanager
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import tempfile
import time
from urllib.parse import parse_qs, urlencode, urlsplit

import requests

from common.profile_paths import tokens_file_for

GOOGLE_HEALTH_SCOPES = tuple(
    f"https://www.googleapis.com/auth/googlehealth.{name}.readonly"
    for name in ("activity_and_fitness", "health_metrics_and_measurements", "sleep", "profile", "settings", "nutrition")
)
GOOGLE_HEALTH_SCOPE_TEXT = " ".join(GOOGLE_HEALTH_SCOPES)
DEFAULT_REDIRECT_URI = "http://localhost:8080/callback"
TOKEN_URL = "https://oauth2.googleapis.com/token"
IDENTITY_URL = "https://health.googleapis.com/v4/users/me/identity"
FITBIT_REVOKE_URL = "https://api.fitbit.com/oauth2/revoke"
SIGNUP_URL = "https://fitbit.google.com/auth/signup"
OAUTH_LIFETIME_SECONDS = 600
TIMEOUT = 30


class GoogleHealthAuthError(RuntimeError):
    """An actionable, token-free error safe to show in the administration UI."""

    def __init__(self, message, code="google_health_auth_failed", status=400, **details):
        super().__init__(message)
        self.code = code
        self.status = status
        self.details = details

    def as_dict(self):
        return {"error": str(self), "code": self.code, **self.details}


def _token_path(profile_id=None, tokens_path=None):
    if profile_id is not None and not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", profile_id):
        raise GoogleHealthAuthError("Invalid profile name.")
    return Path(tokens_path or tokens_file_for(profile_id))


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as handle:
            value = json.load(handle)
    except FileNotFoundError:
        return {}
    except (ValueError, OSError) as exc:
        raise GoogleHealthAuthError("无法读取授权配置，请检查档案授权文件。") from exc
    if not isinstance(value, dict):
        raise GoogleHealthAuthError("授权配置格式错误。")
    return value


def _write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            temp_name = handle.name
            os.fchmod(handle.fileno(), 0o600)
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)


def _validate_redirect_uri(value):
    value = str(value or DEFAULT_REDIRECT_URI).strip()
    parsed = urlsplit(value)
    if (not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.scheme not in {"https", "http"}
            or (parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"})):
        raise GoogleHealthAuthError("Redirect URI 必须是 HTTPS 地址或本机 HTTP 回调地址，且不能包含查询参数。")
    return value


def resolve_google_credentials(profile_id=None, *, tokens_path=None):
    """Load separate Google credentials; Fitbit client.json is never a fallback."""
    path = _token_path(profile_id, tokens_path).with_name("google_client.json")
    configured = _read_json(path)
    # Accept Google's downloaded web/installed client JSON as well as our own format.
    configured = configured.get("web", configured.get("installed", configured))
    env_id = os.getenv("GOOGLE_HEALTH_CLIENT_ID", "").strip()
    env_secret = os.getenv("GOOGLE_HEALTH_CLIENT_SECRET", "").strip()
    cid = env_id if env_id or env_secret else str(configured.get("client_id", "")).strip()
    secret = env_secret if env_id or env_secret else str(configured.get("client_secret", "")).strip()
    if not cid or not secret:
        raise GoogleHealthAuthError(
            "请先配置 Google Cloud OAuth 客户端 ID 和密钥；原 Fitbit 应用凭据不能用于 Google Health。",
            "google_credentials_missing",
        )
    redirects = configured.get("redirect_uris") or []
    redirect = os.getenv("GOOGLE_HEALTH_REDIRECT_URI", "").strip() or configured.get("redirect_uri")
    redirect = redirect or (redirects[0] if redirects else DEFAULT_REDIRECT_URI)
    return {"client_id": cid, "client_secret": secret, "redirect_uri": _validate_redirect_uri(redirect)}


def save_google_credentials(profile_id, client_id, client_secret, redirect_uri=None):
    cid, secret = str(client_id or "").strip(), str(client_secret or "").strip()
    if not cid or not secret:
        raise GoogleHealthAuthError("Google OAuth 客户端 ID 和密钥不能为空。", "google_credentials_missing")
    if not cid.endswith(".apps.googleusercontent.com"):
        raise GoogleHealthAuthError("请输入 Google Cloud OAuth 客户端 ID（以 .apps.googleusercontent.com 结尾）。")
    path = _token_path(profile_id)
    credentials = {"client_id": cid, "client_secret": secret,
                   "redirect_uri": _validate_redirect_uri(redirect_uri)}
    with profile_migration_lock(path):
        _write_json(path.with_name("google_client.json"), credentials)
        path.with_name("google_oauth_pending.json").unlink(missing_ok=True)


@contextmanager
def profile_migration_lock(tokens_path):
    """Use the same process lock as backend manual/automatic profile syncing."""
    lock_path = Path(tokens_path).parent.parent / "cache" / ".fetch.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise GoogleHealthAuthError("当前档案正在同步，请等待同步完成后重试授权。", "sync_in_progress", 409) from exc
        yield
    finally:
        os.close(fd)


@contextmanager
def profile_token_lock(tokens_path):
    """Serialize refresh and consent even when invoked directly from the CLI.

    Sync holds .fetch.lock externally. Always acquire .fetch.lock before this lock
    when both are needed, and never acquire .fetch.lock inside a token refresh.
    """
    lock_path = Path(tokens_path).with_name(".oauth.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise GoogleHealthAuthError("此档案正在刷新令牌或完成授权，请稍后重试。", "oauth_in_progress", 409) from exc
        yield
    finally:
        os.close(fd)


def begin_google_authorization(profile_id, *, session_binding=""):
    path = _token_path(profile_id)
    credentials = resolve_google_credentials(profile_id)
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    pending = {"state": state, "code_verifier": verifier, "created_at": time.time(),
               "client_id": credentials["client_id"], "redirect_uri": credentials["redirect_uri"],
               "session_binding": hashlib.sha256(session_binding.encode()).hexdigest()}
    with profile_migration_lock(path):
        _write_json(path.with_name("google_oauth_pending.json"), pending)
    params = {"client_id": credentials["client_id"], "redirect_uri": credentials["redirect_uri"],
              "response_type": "code", "scope": GOOGLE_HEALTH_SCOPE_TEXT,
              "access_type": "offline", "prompt": "consent", "state": state,
              "code_challenge": challenge, "code_challenge_method": "S256"}
    return {"mode": "manual", "provider": "google", "oauth_type": "google",
            "auth_url": "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params),
            "redirect_uri": credentials["redirect_uri"], "required_scopes": list(GOOGLE_HEALTH_SCOPES),
            "message": "请使用 Google 账号授权，然后粘贴包含 code 和 state 的完整回调地址。"}


def _json_response(response, action):
    try:
        payload = response.json()
    except ValueError as exc:
        raise GoogleHealthAuthError(f"{action}返回了无法解析的响应，请稍后重试。", status=502) from exc
    if not isinstance(payload, dict):
        raise GoogleHealthAuthError(f"{action}返回格式错误。", status=502)
    return payload


def _request(session, method, url, **kwargs):
    try:
        return getattr(session, method)(url, timeout=TIMEOUT, **kwargs)
    except requests.RequestException as exc:
        raise GoogleHealthAuthError("无法连接授权服务，请检查网络后重试。", "oauth_network_error", 502) from exc


def _granted_scopes(tokens):
    scope = tokens.get("scope", "")
    return sorted(set(scope.split() if isinstance(scope, str) else []))


def _legacy_revocation(path, http):
    pending_path = path.with_name("fitbit_revocation_pending.json")
    pending = _read_json(pending_path)
    if not pending:
        return True
    credentials = _read_json(path.with_name("client.json"))
    cid = os.getenv("FITBIT_CLIENT_ID", "").strip() or credentials.get("client_id")
    secret = os.getenv("FITBIT_CLIENT_SECRET", "").strip() or credentials.get("client_secret")
    if not cid or not secret:
        return False
    try:
        response = http.post(FITBIT_REVOKE_URL, auth=(cid, secret),
                             data={"token": pending["token"]}, timeout=TIMEOUT)
    except requests.RequestException:
        return False
    # Fitbit returns 200 for a successful revocation. Invalid/expired tokens are
    # already unusable, but other failures must remain visible and be retried.
    revoked = response.status_code == 200
    if response.status_code in {400, 401}:
        try:
            revoked = any(error.get("errorType") == "invalid_token"
                          for error in response.json().get("errors", []))
        except (ValueError, AttributeError):
            pass
    if revoked:
        pending_path.unlink(missing_ok=True)
    return revoked


def complete_google_authorization(profile_id, redirect_url, *, session_binding="", session=None):
    """Validate state, granted scopes and identity before replacing active tokens."""
    path = _token_path(profile_id)
    http = session or requests
    with profile_migration_lock(path), profile_token_lock(path):
        pending_path = path.with_name("google_oauth_pending.json")
        pending = _read_json(pending_path)
        if not pending or time.time() - pending.get("created_at", 0) > OAUTH_LIFETIME_SECONDS:
            raise GoogleHealthAuthError("授权请求已过期，请重新点击 Google 授权。", "oauth_state_expired")
        parsed = urlsplit(str(redirect_url or "").strip())
        query = parse_qs(parsed.query, keep_blank_values=True)
        expected = urlsplit(pending["redirect_uri"])
        if ((parsed.scheme, parsed.netloc, parsed.path) != (expected.scheme, expected.netloc, expected.path)
                or parsed.fragment or len(query.get("state", [])) != 1
                or not hmac.compare_digest(query["state"][0], pending["state"])
                or not hmac.compare_digest(pending["session_binding"], hashlib.sha256(session_binding.encode()).hexdigest())):
            raise GoogleHealthAuthError("回调地址或授权 state 不匹配，请粘贴本次授权后的完整地址。", "oauth_state_invalid")
        if query.get("error"):
            pending_path.unlink(missing_ok=True)
            raise GoogleHealthAuthError("Google 授权未完成，请重新授权并选择需要的数据权限。", "oauth_consent_denied")
        if len(query.get("code", [])) != 1 or not query["code"][0]:
            raise GoogleHealthAuthError("请粘贴包含 code 和 state 的完整回调地址。", "oauth_code_missing")
        credentials = resolve_google_credentials(profile_id)
        if credentials["client_id"] != pending["client_id"] or credentials["redirect_uri"] != pending["redirect_uri"]:
            raise GoogleHealthAuthError("客户端配置已改变，请重新发起 Google 授权。", "oauth_state_invalid")
        # Consume before exchange to prevent a replay. A failed exchange needs a new consent flow.
        pending_path.unlink()
        old = _read_json(path)
        response = _request(http, "post", TOKEN_URL, data={
            "grant_type": "authorization_code", "code": query["code"][0],
            "client_id": credentials["client_id"], "client_secret": credentials["client_secret"],
            "redirect_uri": pending["redirect_uri"], "code_verifier": pending["code_verifier"],
        })
        token = _json_response(response, "Google 授权服务")
        if response.status_code != 200 or not token.get("access_token"):
            raise GoogleHealthAuthError("Google 授权码无效或已过期，请重新授权；同时检查客户端和回调地址配置。", "oauth_exchange_failed")
        scopes = _granted_scopes(token)
        missing = sorted(set(GOOGLE_HEALTH_SCOPES) - set(scopes))
        if not set(GOOGLE_HEALTH_SCOPES).intersection(scopes):
            raise GoogleHealthAuthError("尚未授予任何仪表盘所需权限，请重新授权并至少选择一类数据。",
                                       "missing_permissions", missing_scopes=list(GOOGLE_HEALTH_SCOPES))
        response = _request(http, "get", IDENTITY_URL,
                            headers={"Authorization": "Bearer " + token["access_token"], "Accept": "application/json"})
        identity = _json_response(response, "Google Health")
        if response.status_code != 200:
            details = identity.get("error", {}).get("details", [])
            if any(item.get("reason") == "ACCOUNT_NOT_LINKED" for item in details if isinstance(item, dict)):
                raise GoogleHealthAuthError(
                    "此 Google 账号尚未关联 Google Health。请在 Google Health 手机应用中登录并创建资料，或将原 Fitbit 账号迁移到此 Google 账号，然后重试。",
                    "account_not_linked", redirect_uri=SIGNUP_URL)
            raise GoogleHealthAuthError("无法验证 Google Health 账号。请确认项目已启用 Google Health API 且账号有访问权限。", "identity_check_failed")
        health_id = identity.get("healthUserId")
        legacy_id = identity.get("legacyUserId")
        if not isinstance(health_id, str) or not health_id:
            raise GoogleHealthAuthError("Google Health 未返回有效用户标识，未切换现有连接。", "identity_check_failed")
        old_google = old.get("oauth_type") == "google"
        legacy_connected = not old_google and bool(old.get("access_token") or old.get("refresh_token"))
        if old_google and old.get("health_user_id") != health_id:
            raise GoogleHealthAuthError("Google Health 账号与当前档案不一致，请使用原账号授权或创建新档案。", "account_mismatch")
        if legacy_connected and (not old.get("user_id") or old["user_id"] != legacy_id):
            raise GoogleHealthAuthError("Google Health 账号与此档案原 Fitbit 用户标识不一致，无法合并历史数据。请使用对应账号，或创建新档案。", "account_mismatch")
        same_client = old_google and old.get("oauth_client_id") == credentials["client_id"]
        refresh_token = token.get("refresh_token") or (old.get("refresh_token") if same_client else None)
        if not refresh_token:
            raise GoogleHealthAuthError("Google 未返回离线刷新令牌，请重新授权并确认同意访问，以便自动同步。", "refresh_token_missing")
        new = {"oauth_type": "google", "access_token": token["access_token"],
               "refresh_token": refresh_token, "token_type": token.get("token_type", "Bearer"),
               "scope": " ".join(scopes), "granted_scopes": scopes, "missing_scopes": missing,
               "expires_at": time.time() + float(token.get("expires_in", 3600)),
               "health_user_id": health_id, "legacy_user_id": legacy_id or old.get("legacy_user_id"),
               "linked_at": time.time(), "oauth_client_id": credentials["client_id"],
               "oauth_client_secret": credentials["client_secret"]}
        if legacy_connected:
            _write_json(path.with_name("fitbit_revocation_pending.json"),
                        {"token": old.get("refresh_token") or old["access_token"]})
        # Once this atomic write succeeds, every sync dispatch uses Google only.
        _write_json(path, new)
        revoked = _legacy_revocation(path, http)
        new["legacy_revocation_pending"] = not revoked
        _write_json(path, new)
        warnings = []
        if missing:
            warnings.append("部分数据权限未授予，对应指标暂不更新；可再次授权补充权限。")
        if not revoked:
            warnings.append("Google Health 已连接且 Fitbit 同步已停用，但旧令牌撤销失败；下次刷新将重试撤销。")
        return {"message": "Google Health 授权完成，已切换数据来源。", "oauth_type": "google",
                "health_user_id": health_id, "granted_scopes": scopes, "missing_scopes": missing,
                "legacy_revocation_pending": not revoked, "warning": " ".join(warnings)}


def refresh_google_token(profile_id=None, *, tokens_path=None, session=None, _lock_held=False):
    """Refresh Google credentials, preserving identity, scopes and refresh token."""
    path = _token_path(profile_id, tokens_path)
    if not _lock_held:
        with profile_token_lock(path):
            return refresh_google_token(profile_id, tokens_path=path, session=session, _lock_held=True)
    old = _read_json(path)
    if old.get("oauth_type") != "google" or not old.get("refresh_token"):
        raise GoogleHealthAuthError("此档案尚未完成 Google Health 授权。", "google_authorization_required")
    if old.get("oauth_client_id") and old.get("oauth_client_secret"):
        credentials = {"client_id": old["oauth_client_id"], "client_secret": old["oauth_client_secret"]}
    else:
        credentials = resolve_google_credentials(profile_id, tokens_path=path)
        if old.get("oauth_client_id") and old["oauth_client_id"] != credentials["client_id"]:
            raise GoogleHealthAuthError("Google 客户端已更改，请重新授权以继续同步。", "google_reauthorization_required")
    http = session or requests
    response = _request(http, "post", TOKEN_URL, data={"grant_type": "refresh_token",
                        "refresh_token": old["refresh_token"], "client_id": credentials["client_id"],
                        "client_secret": credentials["client_secret"]})
    token = _json_response(response, "Google 授权服务")
    if response.status_code != 200 or not token.get("access_token"):
        if token.get("error") == "invalid_grant":
            raise GoogleHealthAuthError("Google 授权已失效，请重新授权。测试模式的刷新令牌通常在 7 天后到期。", "google_reauthorization_required")
        raise GoogleHealthAuthError("Google 令牌刷新失败，请检查客户端配置并稍后重试。", "google_refresh_failed", 502)
    updated = {**old, "access_token": token["access_token"],
               "refresh_token": token.get("refresh_token") or old["refresh_token"],
               "expires_at": time.time() + float(token.get("expires_in", 3600))}
    if "scope" in token:
        updated["scope"] = token["scope"]
        updated["granted_scopes"] = _granted_scopes(token)
        updated["missing_scopes"] = sorted(set(GOOGLE_HEALTH_SCOPES) - set(updated["granted_scopes"]))
    updated["legacy_revocation_pending"] = not _legacy_revocation(path, http)
    _write_json(path, updated)
    return updated["access_token"]
