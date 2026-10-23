"""Public read-only HTTP boundary; account operations are CLI-only."""
from flask import jsonify, request
from common.profile_paths import owner_profile_id, profile_path_for, validate_profile_id


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
    return valid_profile_id(profile_id)


def install_security(app):
    validate_profile_id(app.config['OWNER_PROFILE_ID'])
    if app.config['DATA_ACCESS_MODE'] != 'public':
        raise RuntimeError('Fitflare is public read-only. Set FITFLARE_DATA_ACCESS=public explicitly to publish the configured account.')
    app.before_request(guard_request)
    app.after_request(response_headers)


def guard_request():
    if request.method not in {'GET', 'HEAD', 'OPTIONS'}:
        return jsonify(error='This website is read-only', code='read_only'), 405
    if request.path.startswith('/api/public/v1/me') and any(name in request.args for name in ('profile', 'profileName', 'profile_id')):
        return jsonify(error='The personal API does not accept an account selector'), 400
    profile = (request.view_args or {}).get('profile_id')
    if profile is not None and not valid_profile_id(profile):
        return jsonify(error='Profile not found'), 404


def response_headers(response):
    if request.path.startswith('/api/') or request.path.startswith('/profiles/'):
        response.headers['Cache-Control'] = 'private, no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'same-origin'
    response.headers['X-Frame-Options'] = 'DENY'
    if request.blueprint == 'public_api':
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Methods'] = 'GET, HEAD, OPTIONS'
    return response
