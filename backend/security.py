"""One access boundary for UI data, public API, CSV and management routes."""
import hmac
import secrets
import threading
import time
from collections import OrderedDict, deque
from functools import wraps
from flask import Blueprint, current_app, jsonify, request, session
from werkzeug.security import check_password_hash
from common.profile_paths import owner_profile_id, profile_path_for, validate_profile_id

bp = Blueprint('session', __name__)


def is_admin():
    return bool(current_app.config['ADMIN_AUTH_CONFIGURED'] and session.get('is_admin'))


def valid_profile_id(value):
    if value != owner_profile_id():
        return False
    try:
        validate_profile_id(value)
        for subdir in ('auth', 'cache', 'csv'):
            profile_path_for(value, subdir)
        return True
    except (ValueError, OSError):
        return False


def profile_is_visible(profile_id):
    allowed = current_app.config['PUBLIC_PROFILE_IDS']
    return valid_profile_id(profile_id) and (is_admin() or allowed is None or profile_id in allowed)


def session_payload():
    return {
        'configured': current_app.config['ADMIN_AUTH_CONFIGURED'],
        'authenticated': is_admin(),
        'csrf_token': session.get('csrf_token') if is_admin() else None,
        'data_access': current_app.config['DATA_ACCESS_MODE'],
    }


def auth_error(message, status, code):
    return jsonify(error=message, code=code, **session_payload()), status


def require_admin(csrf=False):
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            if not current_app.config['ADMIN_AUTH_CONFIGURED']:
                return auth_error('管理员口令尚未配置，管理功能已停用。', 503, 'admin_not_configured')
            if not is_admin():
                return auth_error('需要管理员登录。', 401, 'admin_auth_required')
            if csrf and request.method not in {'GET', 'HEAD', 'OPTIONS'}:
                expected = str(session.get('csrf_token') or '')
                supplied = request.headers.get('X-FitBaus-CSRF', '')
                if not expected or not hmac.compare_digest(supplied, expected):
                    return auth_error('管理员会话已失效，请重新登录。', 403, 'invalid_admin_csrf')
            return function(*args, **kwargs)
        return wrapped
    return decorate


def install_security(app):
    validate_profile_id(app.config['OWNER_PROFILE_ID'])
    app.config['ADMIN_AUTH_CONFIGURED'] = bool(app.config['ADMIN_PASSWORD'] or app.config['ADMIN_PASSWORD_HASH'])
    if app.config['DATA_ACCESS_MODE'] not in {'public', 'private'}:
        raise RuntimeError('FITBAUS_DATA_ACCESS must be public or private')
    if app.config['DATA_ACCESS_MODE'] == 'private' and not app.config['ADMIN_AUTH_CONFIGURED']:
        raise RuntimeError('Private mode requires an administrator password or hash')
    app.extensions['login_attempts'] = OrderedDict()
    app.extensions['login_lock'] = threading.Lock()
    app.before_request(guard_request)
    app.after_request(response_headers)


def guard_request():
    if request.path in {'/api/create-profile', '/api/delete-profile'}:
        return jsonify(error='Resource not found'), 404
    read_data = request.blueprint in {'public_api', 'dashboard'}
    docs = request.endpoint in {'public_api.public_api_docs', 'public_api.public_api_docs_markdown', 'public_api.public_api_openapi'}
    if read_data and not docs and current_app.config['DATA_ACCESS_MODE'] == 'private' and not is_admin():
        return auth_error('此站点的数据仅供管理员查看，请先登录。', 401, 'private_data')
    if request.path.startswith('/api/public/v1/me') and any(name in request.args for name in ('profile', 'profileName', 'profile_id')):
        return jsonify(error='The personal API does not accept an account selector'), 400
    profile = (request.view_args or {}).get('profile_id')
    if profile is not None:
        if not valid_profile_id(profile) or (read_data and not profile_is_visible(profile)):
            return jsonify(error='Profile not found'), 404
    if request.blueprint == 'admin' and request.method == 'POST' and is_admin():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify(error='JSON object required'), 400
        for name in ('profile', 'profileName', 'profile_id'):
            if name in data and not valid_profile_id(data[name]):
                return jsonify(error='Only the configured personal account is available'), 400


def response_headers(response):
    # Health responses must be revalidated at the application, never by a shared cache.
    if request.path.startswith('/api/') or request.path.startswith('/profiles/'):
        response.headers['Cache-Control'] = 'private, no-store'
        response.vary.add('Cookie')
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'same-origin'
    response.headers['X-Frame-Options'] = 'DENY'
    # Preserve anonymous public API use; do not grant CORS on admin/private responses.
    if request.blueprint == 'public_api' and current_app.config['DATA_ACCESS_MODE'] == 'public' and not is_admin():
        response.headers['Access-Control-Allow-Origin'] = '*'
    return response


@bp.get('/api/admin/session')
def admin_session():
    return jsonify(session_payload())


@bp.post('/api/admin/login')
def admin_login():
    if not current_app.config['ADMIN_AUTH_CONFIGURED']:
        return auth_error('管理员口令尚未配置，管理功能已停用。', 503, 'admin_not_configured')
    data = request.get_json(silent=True)
    password = data.get('password') if isinstance(data, dict) else None
    if not isinstance(password, str) or not password.strip():
        return jsonify(error='管理员口令不能为空。'), 400
    now = time.monotonic()
    # The reverse proxy is intentionally NOT trusted for arbitrary forwarded IP headers.
    key = request.remote_addr or 'unknown'
    with current_app.extensions['login_lock']:
        attempts = current_app.extensions['login_attempts']
        recent = attempts.setdefault(key, deque())
        while recent and recent[0] <= now - 60:
            recent.popleft()
        if len(recent) >= 5:
            response = jsonify(error='尝试过于频繁，请稍后重试。', code='login_rate_limited')
            response.status_code = 429
            response.headers['Retry-After'] = str(max(1, int(60 - (now - recent[0])) + 1))
            return response
        recent.append(now)
        attempts.move_to_end(key)
        while len(attempts) > 1024:
            attempts.popitem(last=False)
    password_hash = current_app.config['ADMIN_PASSWORD_HASH']
    try:
        valid = check_password_hash(password_hash, password.strip()) if password_hash else hmac.compare_digest(password.strip(), current_app.config['ADMIN_PASSWORD'])
    except (ValueError, TypeError):
        valid = False
    if not valid:
        return auth_error('管理员口令错误。', 401, 'admin_login_failed')
    with current_app.extensions['login_lock']:
        current_app.extensions['login_attempts'].pop(key, None)
    session.clear()
    session.permanent = True
    session['is_admin'] = True
    session['csrf_token'] = secrets.token_urlsafe(24)
    return jsonify(message='已进入管理员模式。', **session_payload())


@bp.post('/api/admin/logout')
@require_admin(csrf=True)
def admin_logout():
    session.clear()
    return jsonify(message='已退出管理员模式。', **session_payload())
