"""Authenticated setup, Google Health OAuth and sync for the configured personal account."""
import json
import os
import subprocess
import threading
from datetime import datetime
from flask import Blueprint, current_app, jsonify, request, session
from common.dashboard_cache import build_dashboard_cache
from common.google_health_auth import (
    GoogleHealthAuthError,
    begin_google_authorization,
    complete_google_authorization,
    resolve_google_credentials,
    save_google_credentials,
)
from common.profile_paths import owner_profile_id, profile_path_for, tokens_file_for
from .security import require_admin, valid_profile_id
from .sync import run_fetch_script
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


def _google_configured():
    try:
        resolve_google_credentials(owner_profile_id())
        return True
    except GoogleHealthAuthError:
        return False


def account_status():
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
    google = tokens.get('oauth_type') == 'google'
    return {'profile_id': owner_profile_id(), 'has_data': cache_exists or has_csv, 'configured': _google_configured(),
            'authorized': google and bool(tokens.get('refresh_token')), 'provider': 'google_health' if google else
            ('fitbit' if tokens.get('refresh_token') else None)}


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
    """Save the Google OAuth client; the active connection changes only after consent succeeds."""
    data = request.get_json()
    if os.getenv('GOOGLE_HEALTH_CLIENT_ID', '').strip() or os.getenv('GOOGLE_HEALTH_CLIENT_SECRET', '').strip():
        return jsonify(error='Google 客户端由环境变量管理，请在部署配置中修改后重启服务。', code='credentials_managed_by_environment'), 409
    try:
        profile = owner_profile_id()
        with jobs.lock:
            for directory in ('auth', 'csv', 'cache'):
                profile_path_for(profile, directory).mkdir(mode=0o700, parents=True, exist_ok=True)
            save_google_credentials(profile, data.get('clientId'), data.get('clientSecret'), data.get('redirectUri'))
        return jsonify(message='Google OAuth 客户端已保存，请继续 Google Health 授权。', **account_status())
    except GoogleHealthAuthError as exc:
        return jsonify(exc.as_dict()), exc.status
    except (OSError, ValueError):
        current_app.logger.exception('Unable to save personal account settings')
        return jsonify(error='无法保存账户配置，请检查数据目录权限。'), 500


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


@bp.route('/api/authorize', methods=['GET', 'POST'])
@bp.route('/api/authorize/<profile_id>', methods=['GET', 'POST'])
@require_admin(csrf=True)
def start_authorization(profile_id=None):
    """Start Google consent; the hosted UI completes it by pasting the full redirect URL."""
    profile_id = owner_profile_id()
    if not profile_path_for(profile_id).is_dir():
        return jsonify(error='Configure your account first'), 404
    try:
        return jsonify(begin_google_authorization(profile_id, session_binding=session.get('csrf_token', '')))
    except GoogleHealthAuthError as exc:
        return jsonify(exc.as_dict()), exc.status
    except OSError:
        return jsonify(error='无法保存授权请求，请确认账户目录可写。'), 500


@bp.get('/api/authorize-status/<job_id>')
@require_admin()
def authorize_status(job_id):
    with jobs.lock:
        job = _owner_job(jobs.auth_jobs, job_id)
        return jsonify(dict(job)) if job else (jsonify(error='Job not found'), 404)


@bp.post('/api/authorize-exchange')
@require_admin(csrf=True)
def authorize_exchange():
    """Validate state, granted permissions and account before switching providers."""
    data = request.get_json()
    if 'redirectUrl' in data and not isinstance(data['redirectUrl'], str):
        return jsonify(error='Redirect URL must be a string'), 400
    profile_id = owner_profile_id()
    if not profile_path_for(profile_id).is_dir():
        return jsonify(error='Configure your account first'), 404
    try:
        result = complete_google_authorization(profile_id, data.get('redirectUrl'), session_binding=session.get('csrf_token', ''))
        # OAuth has already completed; a cache failure must not suggest repeating consent.
        try:
            build_dashboard_cache(profile_id)
        except Exception:
            result['warning'] = ' '.join(filter(None, (
                result.get('warning'),
                '授权已完成，但仪表盘缓存刷新失败；请稍后同步数据或重建缓存。',
            )))
        return jsonify(result)
    except GoogleHealthAuthError as exc:
        return jsonify(exc.as_dict()), exc.status
    except OSError:
        return jsonify(error='无法保存授权信息，请检查账户目录权限后重新授权。'), 500
