"""Profile discovery and the shared public-data visibility rules."""
from pathlib import Path
from flask import current_app
from common.dashboard_cache import build_profile_cards, load_dashboard_cache
from .security import profile_is_visible


def visible_profile_ids():
    root = Path(current_app.config['PROFILES_DIR'])
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir() and profile_is_visible(p.name))


def visible_profile_cards():
    allowed = visible_profile_ids()
    return build_profile_cards(allowed) if allowed else []


def load_public_dashboard(profile_id):
    if not profile_is_visible(profile_id) or profile_id not in visible_profile_ids():
        return None
    return load_dashboard_cache(profile_id, rebuild_if_missing=True)
