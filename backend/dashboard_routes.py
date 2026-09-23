"""Dashboard read endpoints used by the browser."""
import os
import json
from datetime import datetime
from flask import Blueprint, jsonify, request, current_app
from common.dashboard_cache import load_dashboard_cache
from common.public_api import public_dashboard_payload, build_table_payload, parse_int_arg
from .security import is_admin, profile_is_visible
from common.profile_paths import owner_profile_id, profile_path_for
from .repository import visible_profile_ids, visible_profile_cards as build_profile_cards

bp = Blueprint('dashboard', __name__)

@bp.route('/api/profiles')
def list_profiles():
    """List available profiles with creation dates"""
    profiles = []
    profiles_dir = current_app.config['PROFILES_DIR']

    if os.path.exists(profiles_dir):
        for entry in visible_profile_ids():
            profile_path = os.path.join(profiles_dir, entry)
            if os.path.isdir(profile_path) and os.path.exists(os.path.join(profile_path, 'auth', 'tokens.json')):
                # Try to get creation date from client.json
                client_file = os.path.join(profile_path, 'auth', 'client.json')
                creation_date = 'Unknown'

                if os.path.exists(client_file):
                    try:
                        with open(client_file, 'r') as f:
                            client_data = json.load(f)
                            if 'created_at' in client_data:
                                # Parse ISO format and format for display
                                created_dt = datetime.fromisoformat(client_data['created_at'])
                                creation_date = created_dt.strftime('%Y-%m-%d %H:%M')
                    except Exception as e:
                        print(f"Error reading creation date for {entry}: {e}")

                profiles.append({
                    'name': entry,
                    'created': creation_date
                })

    # Sort by profile name
    profiles.sort(key=lambda x: x['name'])
    return jsonify(profiles)


@bp.route('/api/dashboard')
@bp.route('/api/dashboard/<profile_id>')
def dashboard(profile_id=None):
    """Return the unified dashboard cache for one profile."""
    profile_id = owner_profile_id()
    if not profile_is_visible(profile_id):
        return jsonify(error='Account not found'), 404
    try:
        profile_dir = profile_path_for(profile_id)
        if not os.path.isdir(profile_dir):
            return jsonify({'error': f'Profile "{profile_id}" not found'}), 404
        payload = load_dashboard_cache(profile_id, rebuild_if_missing=True)
        payload = dict(payload if is_admin() else public_dashboard_payload(payload))
        if request.args.get('tables') == 'none':
            payload.pop('tables', None)
        return jsonify(payload)
    except Exception as e:
        print(f"Error building dashboard for {profile_id}: {e}")
        return jsonify({'error': f'Failed to build dashboard: {str(e)}'}), 500


@bp.route('/api/profile-summaries')
def profile_summaries():
    """Return lightweight summary cards for all profiles."""
    try:
        return jsonify(build_profile_cards())
    except Exception as e:
        print(f"Error building profile summaries: {e}")
        return jsonify({'error': f'Failed to build profile summaries: {str(e)}'}), 500


@bp.get('/api/tables/<table_key>')
@bp.get('/api/tables/<profile_id>/<table_key>')
def dashboard_table(table_key, profile_id=None):
    profile_id = owner_profile_id()
    from .repository import load_public_dashboard
    payload = load_public_dashboard(profile_id)
    if payload is None:
        return jsonify(error='Profile not found'), 404
    try:
        rows, meta = build_table_payload(
            payload, table_key,
            offset=parse_int_arg(request.args.get('offset'), default=0, minimum=0, maximum=100000),
            limit=parse_int_arg(request.args.get('limit'), default=20, minimum=1, maximum=100),
        )
    except KeyError:
        return jsonify(error='Table not found'), 404
    return jsonify(**rows, meta=meta)


@bp.get('/api/account')
def personal_account():
    from .admin_routes import account_status
    try:
        return jsonify(account_status())
    except (OSError, ValueError):
        return jsonify(error='Unable to read personal account settings'), 500
