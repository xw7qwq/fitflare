"""Authenticated setup, OAuth and sync for the configured personal account."""
import json
import os
import subprocess
import sys
import tempfile
import threading
from datetime import datetime
from flask import Blueprint, current_app, jsonify, request
from common.dashboard_cache import build_dashboard_cache
from common.fitbit_scopes import FITBIT_DASHBOARD_SCOPE_TEXT
from common.profile_paths import owner_profile_id, profile_path_for, client_credentials_file_for, tokens_file_for
from .security import require_admin, valid_profile_id
from .sync import run_fetch_script, _prepare_fetch_env
from . import jobs

bp = Blueprint('admin', __name__)


def _read_json(path):
    if not os.path.exists(path):
        return {}
    with open(path, encoding='utf-8') as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError('Account settings must be a JSON object')
    return data


def account_credentials():
    """Use the same environment-first precedence as the Fitbit CLI."""
    client_id = os.getenv('FITBIT_CLIENT_ID', '').strip()
    client_secret = os.getenv('FITBIT_CLIENT_SECRET', '').strip()
    if client_id and client_secret:
        return client_id, client_secret
    saved = _read_json(client_credentials_file_for(owner_profile_id()))
    return str(saved.get('client_id') or '').strip(), str(saved.get('client_secret') or '').strip()


def account_status():
    client_id, client_secret = account_credentials()
    tokens = _read_json(tokens_file_for(owner_profile_id()))
    cache_exists = any(profile_path_for(owner_profile_id(), 'cache', filename).is_file()
                       for filename in ('dashboard.json', 'fitbit_profile_snapshot.json'))
    csv_dir = profile_path_for(owner_profile_id(), 'csv')
    has_csv = False
    if csv_dir.is_dir():
        for candidate in csv_dir.glob('*.csv'):
            path = profile_path_for(owner_profile_id(), 'csv', candidate.name)
            if path.is_file():
                with path.open(encoding='utf-8', errors='replace') as handle:
                    next(handle, None)
                    if any(line.strip() for line in handle):
                        has_csv = True
                        break
    return {'profile_id': owner_profile_id(), 'has_data': cache_exists or has_csv, 'configured': bool(client_id and client_secret),
            'authorized': bool(tokens.get('refresh_token'))}


def _atomic_private_json(path, payload):
    directory = os.path.dirname(path)
    fd, temporary = tempfile.mkstemp(prefix='.client-', suffix='.tmp', dir=directory)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            os.fchmod(handle.fileno(), 0o600)
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _start_background(target, *args):
    app = current_app._get_current_object()
    def run():
        with app.app_context():
            target(*args)
    threading.Thread(target=run, daemon=True).start()


def _owner_job(registry, job_id):
    job = registry.get(job_id)
    return job if job and job.get('profile') == owner_profile_id() else None


@bp.post('/api/account/setup')
@require_admin(csrf=True)
def setup_account():
    data = request.get_json()
    client_id, client_secret = data.get('clientId'), data.get('clientSecret')
    if not all(isinstance(value, str) and value.strip() for value in (client_id, client_secret)):
        return jsonify(error='Client ID and Client Secret are required'), 400
    if os.getenv('FITBIT_CLIENT_ID', '').strip() and os.getenv('FITBIT_CLIENT_SECRET', '').strip():
        return jsonify(error='Fitbit 凭据由环境变量管理，请在部署配置中修改后重启服务。', code='credentials_managed_by_environment'), 409
    try:
        profile = owner_profile_id()
        with jobs.lock:
            path = client_credentials_file_for(profile)
            existing = _read_json(path)
            for directory in ('auth', 'csv', 'cache'):
                profile_path_for(profile, directory).mkdir(mode=0o700, parents=True, exist_ok=True)
            existing.update(client_id=client_id.strip(), client_secret=client_secret.strip())
            existing.setdefault('created_at', datetime.now().isoformat())
            _atomic_private_json(path, existing)
        return jsonify(message='个人账户配置已保存。', **account_status())
    except (OSError, ValueError):
        current_app.logger.exception('Unable to save personal account settings')
        return jsonify(error='Unable to save account settings; check the data directory permissions'), 500


@bp.post('/api/fetch-data')
@require_admin(csrf=True)
def fetch_data():
    profile = owner_profile_id()
    if not valid_profile_id(profile) or not profile_path_for(profile).is_dir():
        return jsonify(error='Configure your account first'), 404
    with jobs.lock:
        if any(job.get('profile') == profile and job.get('status') in {'queued', 'running'} for job in jobs.fetch_jobs.values()):
            return jsonify(error='当前账户已有同步任务在运行。'), 409
        job_id = jobs.create_fetch(profile)
    _start_background(run_fetch_script, profile, job_id)
    return jsonify(job_id=job_id, status='queued', message='Fetch operation started')


@bp.get('/api/fetch-status/<job_id>')
@require_admin()
def fetch_status(job_id):
    with jobs.lock:
        job = _owner_job(jobs.fetch_jobs, job_id)
        return jsonify(dict(job)) if job else (jsonify(error='Job not found'), 404)


@bp.get('/api/fetch-jobs')
@require_admin()
def list_fetch_jobs():
    with jobs.lock:
        return jsonify([dict(job) for job in jobs.fetch_jobs.values() if job.get('profile') == owner_profile_id()])


@bp.post('/api/cancel-fetch/<job_id>')
@require_admin(csrf=True)
def cancel_fetch(job_id):
    with jobs.lock:
        job = _owner_job(jobs.fetch_jobs, job_id)
        if not job:
            return jsonify(error='Job not found'), 404
        if job['status'] not in {'queued', 'running'}:
            return jsonify(error='Job cannot be cancelled'), 400
        proc = jobs.fetch_procs.get(job_id)
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        job.update(status='cancelled', end_time=datetime.now().isoformat(), error='Cancelled by user')
    return jsonify(success=True, message='Fetch operation cancelled')


@bp.route('/api/fetch-logging', methods=['GET', 'POST'])
@require_admin(csrf=True)
def fetch_logging():
    if request.method == 'POST':
        jobs.verbose_logging = bool(request.get_json().get('enabled', True))
    return jsonify(success=True, verbose_logging=jobs.verbose_logging)


@bp.post('/api/rebuild-dashboard')
@bp.post('/api/rebuild-dashboard/<profile_id>')
@require_admin(csrf=True)
def rebuild_dashboard(profile_id=None):
    profile_id = owner_profile_id()
    if not profile_path_for(profile_id).is_dir():
        return jsonify(error='Configure your account first'), 404
    payload = build_dashboard_cache(profile_id)
    return jsonify(message='Dashboard cache rebuilt', generated_at=payload.get('generated_at'))


def run_authorize_script(profile_id, job_id):
    """Legacy local callback support, confined to the configured owner."""
    if profile_id != owner_profile_id() or not valid_profile_id(profile_id):
        return
    job = _owner_job(jobs.auth_jobs, job_id)
    if not job:
        return
    try:
        job.update(status='running', start_time=datetime.now().isoformat())
        result = subprocess.run(
            [sys.executable, 'auth/authorize_fitbit.py', '--profile', profile_id],
            cwd=os.getcwd(), capture_output=True, text=True, encoding='utf-8', errors='replace',
            env=_prepare_fetch_env(profile_id), timeout=900)
        job.update(status='completed' if result.returncode == 0 else 'failed',
                   return_code=result.returncode, output=result.stdout, error=result.stderr)
    except subprocess.TimeoutExpired:
        job.update(status='timeout', error='Authorization timed out after 15 minutes')
    except Exception:
        job.update(status='error', error='Authorization could not be completed')
    finally:
        job['end_time'] = datetime.now().isoformat()


@bp.route('/api/authorize', methods=['GET', 'POST'])
@bp.route('/api/authorize/<profile_id>', methods=['GET', 'POST'])
@require_admin(csrf=True)
def start_authorization(profile_id=None):
    from auth.authorize_fitbit import REDIRECT_URI
    from urllib.parse import urlencode
    profile_id = owner_profile_id()
    client_id, client_secret = account_credentials()
    if not client_id or not client_secret:
        return jsonify(error='Configure your Fitbit Client ID and Client Secret first'), 400
    redirect_uri = os.getenv('FITBIT_REDIRECT_URI', REDIRECT_URI).strip()
    auth_url = 'https://www.fitbit.com/oauth2/authorize?' + urlencode({
        'client_id': client_id, 'response_type': 'code', 'scope': FITBIT_DASHBOARD_SCOPE_TEXT,
        'redirect_uri': redirect_uri})
    if request.method == 'GET':
        # The Fitbit browser redirect is pasted into the hosted personal dashboard.
        return jsonify(mode='manual', auth_url=auth_url, redirect_uri=redirect_uri,
                       message='Open Fitbit, approve access, then paste the redirected URL or code.')
    job_id = jobs.new_id()
    with jobs.lock:
        jobs.auth_jobs[job_id] = dict(id=job_id, profile=profile_id, status='queued',
            created_time=datetime.now().isoformat(), start_time=None, end_time=None,
            return_code=None, output=None, error=None)
    _start_background(run_authorize_script, profile_id, job_id)
    return jsonify(job_id=job_id, status='queued', message='Authorization started')


@bp.get('/api/authorize-status/<job_id>')
@require_admin()
def authorize_status(job_id):
    with jobs.lock:
        job = _owner_job(jobs.auth_jobs, job_id)
        return jsonify(dict(job)) if job else (jsonify(error='Job not found'), 404)


@bp.post('/api/authorize-exchange')
@require_admin(csrf=True)
def authorize_exchange():
    from auth.authorize_fitbit import extract_code_from_url, exchange_code_for_token, REDIRECT_URI
    data = request.get_json()
    if any(name in data and not isinstance(data[name], str) for name in ('code', 'redirectUrl')):
        return jsonify(error='Authorization code and redirect URL must be strings'), 400
    code = data.get('code', '').strip() or extract_code_from_url(data.get('redirectUrl', '').strip()) or ''
    if not code:
        return jsonify(error='Authorization code not found. Paste the redirected URL or the code.'), 400
    client_id, client_secret = account_credentials()
    if not client_id or not client_secret:
        return jsonify(error='Configure your Fitbit Client ID and Client Secret first'), 400
    redirect_uri = os.getenv('FITBIT_REDIRECT_URI', REDIRECT_URI).strip()
    if exchange_code_for_token(code, redirect_uri, client_id, client_secret, profile_id=owner_profile_id()):
        return jsonify(message='Authorization complete and tokens saved.')
    return jsonify(error='Token exchange failed'), 502
