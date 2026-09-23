"""Profile discovery and the shared public-data visibility rules."""
from common.profile_paths import owner_profile_id, profile_path_for
from flask import current_app
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
