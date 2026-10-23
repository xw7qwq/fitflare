"""Profile discovery and the shared public-data visibility rules."""
from common.profile_paths import owner_profile_id, profile_path_for
from common.dashboard_cache import build_profile_cards, load_dashboard_cache
from .security import profile_is_visible


def visible_profile_ids():
    owner = owner_profile_id()
    return [owner] if profile_is_visible(owner) and profile_path_for(owner).is_dir() else []


def visible_profile_cards():
    allowed = visible_profile_ids()
    return build_profile_cards(allowed) if allowed else []


def load_public_dashboard(profile_id):
    if not profile_is_visible(profile_id) or profile_id not in visible_profile_ids():
        return None
    return load_dashboard_cache(profile_id, rebuild_if_missing=True)


def account_status():
    """Public data availability only; never inspect OAuth credentials or tokens."""
    owner = owner_profile_id()
    has_data = any(profile_path_for(owner, 'cache', name).is_file()
                   for name in ('dashboard.json', 'fitbit_profile_snapshot.json'))
    if not has_data:
        for candidate in profile_path_for(owner, 'csv').glob('*.csv'):
            path = profile_path_for(owner, 'csv', candidate.name)
            with path.open(encoding='utf-8', errors='replace') as handle:
                next(handle, None)
                if any(line.strip() for line in handle):
                    has_data = True
                    break
    return {'profile_id': owner, 'has_data': has_data}
