"""Paths for the personal account, with explicit CLI maintenance compatibility."""
import os
import re
from pathlib import Path
from typing import Optional, List

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROFILE_NAME = re.compile(r"[a-zA-Z0-9_-]{1,128}\Z")


def _app_setting(name, default):
    # CLI tools share these helpers without needing an application context.
    from flask import current_app, has_app_context
    return current_app.config.get(name, default) if has_app_context() else default


def validate_profile_id(value: str) -> str:
    if not isinstance(value, str) or not PROFILE_NAME.fullmatch(value):
        raise ValueError("Account ID must contain only letters, numbers, hyphens or underscores")
    return value


def owner_profile_id() -> str:
    return validate_profile_id(_app_setting('OWNER_PROFILE_ID', os.getenv('FITFLARE_PROFILE_ID', 'me').strip()))


def get_active_profile(cli_profile: Optional[str] = None) -> str:
    """Use the configured owner; an explicit --profile remains a CLI maintenance override.

    FITBIT_PROFILE is no longer an implicit account selector. Existing directories
    can be selected for the personal app by setting FITFLARE_PROFILE_ID.
    """
    return validate_profile_id(str(cli_profile).strip()) if cli_profile else owner_profile_id()



def activate_profile_context(profile_id: Optional[str] = None) -> str:
    """Keep explicit CLI maintenance selection consistent through token refreshes."""
    selected = get_active_profile(profile_id)
    token_path = tokens_file_for(selected)
    os.environ['FITFLARE_PROFILE_ID'] = selected
    os.environ['FITBIT_PROFILE'] = selected
    os.environ['FITBIT_TOKENS_FILE'] = token_path
    return token_path


def profiles_root() -> Path:
    return Path(_app_setting('PROFILES_DIR', str(Path(ROOT_DIR) / 'profiles'))).resolve()


def profile_path_for(profile_id: Optional[str] = None, *parts: str) -> Path:
    """Resolve a path without following profile, directory or file symlinks."""
    root = profiles_root()
    path = root / get_active_profile(profile_id)
    for part in ('', *parts):
        if part:
            if Path(part).name != part or part in {'.', '..'} or '\\' in part:
                raise ValueError('Invalid account path')
            path = path / part
        if path.is_symlink():
            raise ValueError('Account paths must not contain symlinks')
    if not path.resolve().is_relative_to(root):
        raise ValueError('Account path escapes data directory')
    return path


def tokens_file_for(profile_id: Optional[str] = None) -> str:
    return str(profile_path_for(profile_id, 'auth', 'tokens.json'))


def client_credentials_file_for(profile_id: Optional[str] = None) -> str:
    return str(profile_path_for(profile_id, 'auth', 'client.json'))


def csv_path_for(profile_id: Optional[str], filename: str) -> str:
    return str(profile_path_for(profile_id, 'csv', filename))


def cache_path_for(profile_id: Optional[str], filename: str) -> str:
    return str(profile_path_for(profile_id, 'cache', filename))


def list_profiles() -> List[str]:
    """Compatibility helper: expose only the configured personal account."""
    owner = owner_profile_id()
    try:
        return [owner] if profile_path_for(owner).is_dir() else []
    except ValueError:
        return []


def resolve_or_prompt_profile(cli_profile: Optional[str] = None) -> str:
    """Resolve the owner without scanning directories or prompting for an account."""
    return get_active_profile(cli_profile)


def ensure_dirs_for_tokens(path: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), mode=0o700, exist_ok=True)


def ensure_dirs_for_csv(path: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), mode=0o700, exist_ok=True)


def ensure_dirs_for_cache(path: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), mode=0o700, exist_ok=True)
