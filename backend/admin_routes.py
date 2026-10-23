"""Authenticated profile, OAuth and manual-sync operations."""
import os
import json
import subprocess
import threading
from datetime import datetime
from flask import Blueprint, jsonify, request, session
from common.dashboard_cache import build_dashboard_cache
from common.google_health_auth import (
    GoogleHealthAuthError,
    begin_google_authorization,
    complete_google_authorization,
    save_google_credentials,
)
from common.profile_paths import tokens_file_for
from .security import require_admin
from .sync import run_fetch_script
from . import jobs

bp = Blueprint('admin', __name__)


@bp.post('/api/fetch-data')
@require_admin(csrf=True)
def fetch_data():
    profile_id = (request.get_json(silent=True) or {}).get('profile')
    from .security import valid_profile_id
    if not valid_profile_id(profile_id) or not os.path.isdir(os.path.join('profiles', profile_id)):
        return jsonify(error='Profile not found'), 404
    with jobs.lock:
        if any(job.get('profile') == profile_id and job.get('status') in {'queued', 'running'} for job in jobs.fetch_jobs.values()):
            return jsonify(error='当前档案已有同步任务在运行。'), 409
        job_id = jobs.create_fetch(profile_id)
    threading.Thread(target=run_fetch_script, args=(profile_id, job_id), daemon=True).start()
    return jsonify(job_id=job_id, status='queued', message='Fetch operation started')


@bp.get('/api/fetch-status/<job_id>')
@require_admin()
def fetch_status(job_id):
    with jobs.lock:
        job = jobs.fetch_jobs.get(job_id)
        return jsonify(dict(job)) if job else (jsonify(error='Job not found'), 404)


@bp.get('/api/fetch-jobs')
@require_admin()
def list_fetch_jobs():
    with jobs.lock:
        return jsonify([dict(job) for job in jobs.fetch_jobs.values()])

@bp.route('/api/create-profile', methods=['POST'])
@require_admin(csrf=True)
def create_profile():
    """New profiles use a separate Google OAuth client, never Fitbit credentials."""
    data = request.get_json() or {}
    profile_name = str(data.get('profileName') or '').strip()
    from .security import valid_profile_id
    if not valid_profile_id(profile_name):
        return jsonify(error='Invalid profile name'), 400
    profile_dir = os.path.dirname(os.path.dirname(tokens_file_for(profile_name)))
    if os.path.exists(profile_dir):
        return jsonify(error=f'Profile "{profile_name}" already exists'), 400
    try:
        # Validate before creating a profile, so failed setup leaves no empty profile.
        from common.google_health_auth import _validate_redirect_uri
        client_id = str(data.get('clientId') or '').strip()
        client_secret = str(data.get('clientSecret') or '').strip()
        if not client_id.endswith('.apps.googleusercontent.com') or not client_secret:
            return jsonify(error='请输入有效的 Google Cloud OAuth 客户端 ID 和密钥。', code='google_credentials_missing'), 400
        redirect_uri = _validate_redirect_uri(data.get('redirectUri'))
        os.makedirs(profile_dir)
        for directory in ('auth', 'csv', 'cache'):
            os.makedirs(os.path.join(profile_dir, directory))
        save_google_credentials(profile_name, client_id, client_secret, redirect_uri)
        return jsonify(message=f'Profile "{profile_name}" created. Connect Google Health to start syncing.',
                       profileName=profile_name, oauth_type='google')
    except GoogleHealthAuthError as exc:
        return jsonify(exc.as_dict()), exc.status
    except OSError:
        return jsonify(error='无法保存档案，请确认 profiles 目录可写。'), 500


@bp.post('/api/google-health-credentials')
@require_admin(csrf=True)
def google_health_credentials():
    """Prepare a migration without changing the currently active connection."""
    data = request.get_json() or {}
    profile_name = str(data.get('profileName') or '').strip()
    from .security import valid_profile_id
    if not valid_profile_id(profile_name) or not os.path.isdir(os.path.dirname(os.path.dirname(tokens_file_for(profile_name)))):
        return jsonify(error='Profile not found'), 404
    try:
        save_google_credentials(profile_name, data.get('clientId'), data.get('clientSecret'), data.get('redirectUri'))
        return jsonify(message='Google OAuth 客户端已保存，请继续 Google Health 授权。')
    except GoogleHealthAuthError as exc:
        return jsonify(exc.as_dict()), exc.status
    except OSError:
        return jsonify(error='无法保存 Google OAuth 配置，请确认档案目录可写。'), 500


@bp.route('/api/delete-profile', methods=['POST'])
@require_admin(csrf=True)
def delete_profile():
    """Delete a specific profile using the reset script"""
    try:
        data = request.get_json()
        profile_name = data.get('profileName', '').strip()

        if not profile_name:
            return jsonify({'error': 'Profile name is required'}), 400

        # Validate profile name (alphanumeric, hyphens, underscores only)
        import re
        if not re.match(r'^[a-zA-Z0-9_-]+$', profile_name):
            return jsonify({'error': 'Invalid profile name format'}), 400

        # Check if profile exists
        profile_dir = f'profiles/{profile_name}'
        if not os.path.exists(profile_dir):
            return jsonify({'error': f'Profile "{profile_name}" not found'}), 404

        # Cancel any running or queued fetch jobs for this profile to avoid recreation during deletion
        try:
            to_cancel = []
            for jid, job in list(jobs.fetch_jobs.items()):
                if job.get('profile') == profile_name and job.get('status') in ('queued', 'running'):
                    to_cancel.append(jid)
            for jid in to_cancel:
                proc = jobs.fetch_procs.get(jid)
                if proc and proc.poll() is None:
                    try:
                        proc.terminate()
                        # Give it a moment to exit
                        try:
                            proc.wait(timeout=5)
                        except Exception:
                            proc.kill()
                    except Exception as e:
                        print(f"Warning: failed to terminate fetch job {jid} for profile {profile_name}: {e}")
                # Mark job as cancelled
                try:
                    jobs.fetch_jobs[jid]['status'] = 'cancelled'
                    jobs.fetch_jobs[jid]['end_time'] = datetime.now().isoformat()
                    jobs.fetch_jobs[jid]['error'] = 'Cancelled due to profile deletion'
                except Exception:
                    pass
        except Exception as e:
            print(f"Warning: error while cancelling fetch jobs for {profile_name}: {e}")

        # Run the reset script with --profile parameter in non-interactive mode
        import subprocess
        result = subprocess.run(
            ['python', 'reset.py', '--profile', profile_name, '--yes'],
            cwd=os.getcwd(),
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=30
        )

        if result.returncode == 0:
            print(f"Successfully deleted profile: {profile_name}")
            # After deletion, try to sync profiles/index.json
            try:
                import sys
                sys.path.append('auth')
                from authorize_fitbit import sync_existing_profiles  # type: ignore
                sync_existing_profiles()
            except Exception as e:
                print(f"Warning: could not sync profiles/index.json after delete: {e}")
            return jsonify({'message': f'Profile "{profile_name}" deleted successfully'})
        else:
            error_msg = result.stderr or result.stdout or 'Unknown error'
            print(f"Failed to delete profile {profile_name}: {error_msg}")
            return jsonify({'error': f'Failed to delete profile: {error_msg}'}), 500

    except subprocess.TimeoutExpired:
        return jsonify({'error': 'Profile deletion timed out'}), 500
    except Exception as e:
        print(f"Error deleting profile: {e}")
        return jsonify({'error': f'Failed to delete profile: {str(e)}'}), 500


@bp.route('/api/cancel-fetch/<job_id>', methods=['POST'])
@require_admin(csrf=True)
def cancel_fetch(job_id):
    """Cancel a running fetch operation"""
    print(f"[DEBUG] Cancel request for job {job_id}")
    print(f"[DEBUG] Current fetch_jobs keys: {list(jobs.fetch_jobs.keys())}")

    if job_id not in jobs.fetch_jobs:
        print(f"[DEBUG] Job {job_id} not found for cancellation")
        return jsonify({'error': 'Job not found'}), 404

    job = jobs.fetch_jobs[job_id]
    print(f"[DEBUG] Job {job_id} status: {job.get('status', 'unknown')}")

    if job['status'] not in ('queued', 'running'):
        print(f"[DEBUG] Job {job_id} cannot be cancelled (status: {job['status']})")
        return jsonify({'error': 'Job cannot be cancelled'}), 400

    try:
        # Terminate the subprocess if it exists
        proc = jobs.fetch_procs.get(job_id)
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                # Give it a moment to exit gracefully
                try:
                    proc.wait(timeout=5)
                except Exception:
                    proc.kill()
            except Exception as e:
                print(f"Warning: failed to terminate fetch job {job_id}: {e}")

        # Mark job as cancelled
        jobs.fetch_jobs[job_id]['status'] = 'cancelled'
        jobs.fetch_jobs[job_id]['end_time'] = datetime.now().isoformat()
        jobs.fetch_jobs[job_id]['error'] = 'Cancelled by user'

        print(f"[DEBUG] Job {job_id} marked as cancelled")
        print(f"[DEBUG] Updated fetch_jobs keys: {list(jobs.fetch_jobs.keys())}")

        return jsonify({
            'success': True,
            'message': 'Fetch operation cancelled'
        })

    except Exception as e:
        return jsonify({'error': f'Failed to cancel job: {str(e)}'}), 500


@bp.route('/api/fetch-logging', methods=['GET', 'POST'])
@require_admin(csrf=True)
def fetch_logging():
    """Get or set verbose fetch logging status"""

    if request.method == 'GET':
        return jsonify({
            'verbose_logging': jobs.verbose_logging,
            'message': 'Verbose fetch logging is ' + ('enabled' if jobs.verbose_logging else 'disabled')
        })

    elif request.method == 'POST':
        data = request.get_json() or {}
        enabled = data.get('enabled', True)
        jobs.verbose_logging = bool(enabled)

        return jsonify({
            'success': True,
            'verbose_logging': jobs.verbose_logging,
            'message': 'Verbose fetch logging ' + ('enabled' if jobs.verbose_logging else 'disabled')
        })


@bp.route('/api/rebuild-dashboard/<profile_id>', methods=['POST'])
@require_admin(csrf=True)
def rebuild_dashboard(profile_id):
    """Force a dashboard cache rebuild for one profile."""
    try:
        profile_dir = os.path.join('profiles', profile_id)
        if not os.path.isdir(profile_dir):
            return jsonify({'error': f'Profile "{profile_id}" not found'}), 404
        payload = build_dashboard_cache(profile_id)
        return jsonify({
            'message': f'Dashboard cache rebuilt for {profile_id}',
            'generated_at': payload.get('generated_at'),
        })
    except Exception as e:
        print(f"Error rebuilding dashboard for {profile_id}: {e}")
        return jsonify({'error': f'Failed to rebuild dashboard: {str(e)}'}), 500


@bp.route('/api/authorize/<profile_id>', methods=['GET', 'POST'])
@require_admin(csrf=True)
def start_authorization(profile_id):
    """Start Google consent; hosted UIs complete by pasting the full redirect URL."""
    if not os.path.isdir(os.path.dirname(os.path.dirname(tokens_file_for(profile_id)))):
        return jsonify(error='Profile not found'), 404
    try:
        return jsonify(begin_google_authorization(profile_id, session_binding=session.get('csrf_token', '')))
    except GoogleHealthAuthError as exc:
        return jsonify(exc.as_dict()), exc.status
    except OSError:
        return jsonify(error='无法保存授权请求，请确认档案目录可写。'), 500


@bp.route('/api/authorize-status/<job_id>')
@require_admin()
def authorize_status(job_id):
    if job_id not in jobs.auth_jobs:
        return jsonify(error='Job not found'), 404
    return jsonify(jobs.auth_jobs[job_id])


@bp.route('/api/authorize-exchange', methods=['POST'])
@require_admin(csrf=True)
def authorize_exchange():
    """Validate state, granted permissions and account before switching providers."""
    data = request.get_json() or {}
    profile_name = str(data.get('profileName') or '').strip()
    from .security import valid_profile_id
    if not valid_profile_id(profile_name) or not os.path.isdir(os.path.dirname(os.path.dirname(tokens_file_for(profile_name)))):
        return jsonify(error='Profile not found'), 404
    try:
        result = complete_google_authorization(
            profile_name, data.get('redirectUrl'), session_binding=session.get('csrf_token', ''),
        )
        # Refresh provider and permission metadata from local tokens immediately.
        # OAuth has already completed; a cache failure must not suggest repeating
        # consent or hide a successful provider transition.
        try:
            build_dashboard_cache(profile_name)
        except Exception:
            result['warning'] = ' '.join(filter(None, (
                result.get('warning'),
                '授权已完成，但仪表盘缓存刷新失败；请稍后同步数据或重建缓存。',
            )))
        return jsonify(result)
    except GoogleHealthAuthError as exc:
        return jsonify(exc.as_dict()), exc.status
    except OSError:
        return jsonify(error='无法保存授权信息，请检查档案目录权限后重新授权。'), 500
