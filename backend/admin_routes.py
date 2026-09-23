"""Authenticated profile, OAuth and manual-sync operations."""
import os
import json
import subprocess
import threading
from datetime import datetime
from flask import Blueprint, jsonify, request
from common.dashboard_cache import build_dashboard_cache
from common.fitbit_scopes import FITBIT_DASHBOARD_SCOPE_TEXT
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
    """Create a new profile with client credentials"""
    try:
        data = request.get_json()
        profile_name = data.get('profileName', '').strip()
        client_id = data.get('clientId', '').strip()
        client_secret = data.get('clientSecret', '').strip()

        # Validate inputs
        if not profile_name or not client_id or not client_secret:
            return jsonify({'error': 'All fields are required'}), 400

        # Validate profile name (alphanumeric, hyphens, underscores only)
        import re
        if not re.match(r'^[a-zA-Z0-9_-]+$', profile_name):
            return jsonify({'error': 'Profile name can only contain letters, numbers, hyphens, and underscores'}), 400

        # Check if profile already exists
        profile_dir = f'profiles/{profile_name}'
        if os.path.exists(profile_dir):
            return jsonify({'error': f'Profile "{profile_name}" already exists'}), 400

        # Create profile directory structure
        try:
            os.makedirs(f'{profile_dir}/auth', exist_ok=True)
            os.makedirs(f'{profile_dir}/csv', exist_ok=True)
            os.makedirs(f'{profile_dir}/cache', exist_ok=True)
        except PermissionError as pe:
            # Provide a helpful message for common Docker-on-Linux bind-mount issues
            msg = (
                "Permission denied creating profile directories. If running with Docker on Linux, "
                "ensure the host 'profiles' directory is writable by the container user (uid 10001). "
                "Try one of: `sudo chown -R 10001:10001 profiles`, or set `user: \"${UID:-10001}:${GID:-10001}\"` "
                "in docker-compose.yml, or relax permissions: `chmod -R 775 profiles`."
            )
            print(f"Error creating profile (permissions): {pe}")
            return jsonify({
                'error': 'Failed to create profile: permission denied',
                'hint': msg
            }), 500

        # Save client credentials with creation timestamp
        client_creds = {
            'client_id': client_id,
            'client_secret': client_secret,
            'created_at': datetime.now().isoformat()
        }

        with open(f'{profile_dir}/auth/client.json', 'w') as f:
            json.dump(client_creds, f, indent=2)

        # Create empty tokens file
        with open(f'{profile_dir}/auth/tokens.json', 'w') as f:
            json.dump({}, f)

        print(f"Created profile: {profile_name}")
        return jsonify({'message': f'Profile "{profile_name}" created successfully', 'profileName': profile_name})

    except PermissionError as e:
        # Catch any remaining permission errors (e.g., opening files)
        print(f"Error creating profile (permissions): {e}")
        return jsonify({
            'error': 'Failed to create profile: permission denied',
            'hint': (
                "Ensure the 'profiles' directory is writable. On Docker/Linux: "
                "`sudo chown -R 10001:10001 profiles` or set `user: \"${UID:-10001}:${GID:-10001}\"` in docker-compose.yml."
            )
        }), 500
    except Exception as e:
        print(f"Error creating profile: {e}")
        return jsonify({'error': f'Failed to create profile: {str(e)}'}), 500


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


def run_authorize_script(profile_id, job_id):
    """Run authorize_fitbit.py in background thread to complete OAuth flow"""
    try:
        jobs.auth_jobs[job_id]['status'] = 'running'
        jobs.auth_jobs[job_id]['start_time'] = datetime.now().isoformat()

        # Ensure profile directory exists (created during create-profile)
        profile_dir = f'profiles/{profile_id}'
        if not os.path.exists(profile_dir):
            jobs.auth_jobs[job_id]['status'] = 'failed'
            jobs.auth_jobs[job_id]['end_time'] = datetime.now().isoformat()
            jobs.auth_jobs[job_id]['error'] = f'Profile {profile_id} not found. Create it first.'
            return

        # Run the authorization script (opens browser locally and saves tokens)
        cmd = ['python', 'auth/authorize_fitbit.py', '--profile', profile_id]
        result = subprocess.run(
            cmd,
            cwd=os.getcwd(),
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=900  # 15 minutes to allow user interaction
        )

        jobs.auth_jobs[job_id]['status'] = 'completed' if result.returncode == 0 else 'failed'
        jobs.auth_jobs[job_id]['end_time'] = datetime.now().isoformat()
        jobs.auth_jobs[job_id]['return_code'] = result.returncode
        jobs.auth_jobs[job_id]['output'] = result.stdout
        jobs.auth_jobs[job_id]['error'] = result.stderr
    except subprocess.TimeoutExpired:
        jobs.auth_jobs[job_id]['status'] = 'timeout'
        jobs.auth_jobs[job_id]['end_time'] = datetime.now().isoformat()
        jobs.auth_jobs[job_id]['error'] = 'Authorization timed out after 15 minutes'
    except Exception as e:
        jobs.auth_jobs[job_id]['status'] = 'error'
        jobs.auth_jobs[job_id]['end_time'] = datetime.now().isoformat()
        jobs.auth_jobs[job_id]['error'] = str(e)


@bp.route('/api/authorize/<profile_id>', methods=['GET', 'POST'])
@require_admin(csrf=True)
def start_authorization(profile_id):
    """
    GET: Return recommended mode and authorization URL.
         If HTTPS localhost redirect is configured but cert/key are missing, return manual mode with URL.
    POST: Start background authorization job that opens a browser and captures the callback automatically.
    """
    try:
        import sys
        sys.path.append('auth')
        from authorize_fitbit import REDIRECT_URI as DEFAULT_REDIRECT_URI  # type: ignore
        from authorize_fitbit import exchange_code_for_token  # noqa: F401 (used by other endpoint)

        # Determine redirect URI and whether HTTPS localhost is usable
        redirect_uri = os.getenv('FITBIT_REDIRECT_URI', DEFAULT_REDIRECT_URI).strip()
        needs_https_local = redirect_uri.startswith('https://localhost:') or redirect_uri.startswith('https://127.0.0.1:')
        cert = os.getenv('FITBIT_SSL_CERT', '').strip()
        key = os.getenv('FITBIT_SSL_KEY', '').strip()
        has_https_creds = bool(cert and key and os.path.exists(cert) and os.path.exists(key))

        # Load client_id for auth URL
        client_file = os.path.join('profiles', profile_id, 'auth', 'client.json')
        if not os.path.exists(client_file):
            return jsonify({'error': f'Client credentials not found for profile {profile_id}'}), 400
        with open(client_file, 'r', encoding='utf-8') as f:
            client_json = json.load(f)
        client_id = client_json.get('client_id', '').strip()
        if not client_id:
            return jsonify({'error': 'Client ID missing in client.json'}), 400

        # Build authorization URL
        from urllib.parse import urlencode
        params = {
            'client_id': client_id,
            'response_type': 'code',
            'scope': FITBIT_DASHBOARD_SCOPE_TEXT,
            'redirect_uri': redirect_uri,
        }
        auth_url = f"https://www.fitbit.com/oauth2/authorize?{urlencode(params)}"

        if request.method == 'GET':
            localhost_redirect = (
                redirect_uri.startswith('http://localhost:')
                or redirect_uri.startswith('http://127.0.0.1:')
                or redirect_uri.startswith('https://localhost:')
                or redirect_uri.startswith('https://127.0.0.1:')
            )

            # For the hosted web UI, localhost callbacks are best handled with the
            # manual flow: open Fitbit in the user's browser, then paste the final
            # redirected URL/code back into the app.
            if localhost_redirect:
                detail = 'Localhost redirect detected: use manual flow.'
                if needs_https_local and not has_https_creds:
                    detail = 'HTTPS localhost redirect without certs: use manual flow.'
                return jsonify({
                    'mode': 'manual',
                    'auth_url': auth_url,
                    'redirect_uri': redirect_uri,
                    'message': detail
                })

            return jsonify({
                'mode': 'background',
                'auth_url': auth_url,
                'redirect_uri': redirect_uri,
                'message': 'Background authorization supported.'
            })

        # POST: start background job
        job_id = jobs.new_id()

        jobs.auth_jobs[job_id] = {
            'id': job_id,
            'profile': profile_id,
            'status': 'queued',
            'created_time': datetime.now().isoformat(),
            'start_time': None,
            'end_time': None,
            'return_code': None,
            'output': None,
            'error': None
        }

        thread = threading.Thread(target=run_authorize_script, args=(profile_id, job_id))
        thread.daemon = False  # Changed from True to False to prevent premature cleanup
        thread.start()

        return jsonify({
            'job_id': job_id,
            'status': 'queued',
            'message': f'Authorization started for profile: {profile_id}'
        })
    except Exception as e:
        return jsonify({'error': f'Failed to start or query authorization: {str(e)}'}), 500


@bp.route('/api/authorize-status/<job_id>')
@require_admin()
def authorize_status(job_id):
    """Get status of an authorization operation"""
    if job_id not in jobs.auth_jobs:
        return jsonify({'error': 'Job not found'}), 404
    return jsonify(jobs.auth_jobs[job_id])


@bp.route('/api/authorize-exchange', methods=['POST'])
@require_admin(csrf=True)
def authorize_exchange():
    """Exchange a pasted redirect URL or code for tokens (manual flow)"""
    try:
        data = request.get_json() or {}
        profile_name = (data.get('profileName') or '').strip()
        pasted_url = (data.get('redirectUrl') or '').strip()
        pasted_code = (data.get('code') or '').strip()

        if not profile_name:
            return jsonify({'error': 'Profile name is required'}), 400

        import sys
        sys.path.append('auth')
        from authorize_fitbit import (
            extract_code_from_url,
            exchange_code_for_token,
            client_credentials_file_for,
            get_active_profile,
            REDIRECT_URI as DEFAULT_REDIRECT_URI,
        )

        # Determine code
        code = pasted_code
        if not code:
            code = extract_code_from_url(pasted_url) or ''
        code = code.strip()
        if not code:
            return jsonify({'error': 'Authorization code not found. Paste the full redirected URL or the code.'}), 400

        # Load client credentials
        cred_path = os.path.join('profiles', profile_name, 'auth', 'client.json')
        if not os.path.exists(cred_path):
            return jsonify({'error': 'Client credentials file not found for this profile'}), 400
        with open(cred_path, 'r', encoding='utf-8') as f:
            cj = json.load(f)
        client_id = (cj.get('client_id') or '').strip()
        client_secret = (cj.get('client_secret') or '').strip()
        if not client_id or not client_secret:
            return jsonify({'error': 'Client credentials are incomplete'}), 400

        redirect_uri = os.getenv('FITBIT_REDIRECT_URI', DEFAULT_REDIRECT_URI).strip()

        # Exchange and save tokens
        ok = exchange_code_for_token(code, redirect_uri, client_id, client_secret, profile_id=profile_name)
        if ok:
            return jsonify({'message': 'Authorization complete and tokens saved.'})
        return jsonify({'error': 'Token exchange failed'}), 500
    except Exception as e:
        return jsonify({'error': f'Failed to exchange code: {str(e)}'}), 500
