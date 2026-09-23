"""Environment configuration, with no application or scheduler side effects."""
import os
import secrets
from datetime import timedelta
from pathlib import Path
from common.profile_paths import ROOT_DIR, validate_profile_id


def env_flag(name, default):
    return os.getenv(name, str(default)).strip().lower() not in {'0', 'false', 'no', 'off'}


def env_int(name, default, minimum):
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except ValueError:
        return default


AUTO_SYNC_ENABLED = env_flag('FITBAUS_AUTO_SYNC_ENABLED', True)
AUTO_SYNC_INTERVAL_SECONDS = env_int('FITBAUS_AUTO_SYNC_INTERVAL_SECONDS', 21600, 60)
AUTO_SYNC_SCAN_INTERVAL_SECONDS = env_int('FITBAUS_AUTO_SYNC_SCAN_INTERVAL_SECONDS', 300, 30)
AUTO_SYNC_STARTUP_DELAY_SECONDS = env_int('FITBAUS_AUTO_SYNC_STARTUP_DELAY_SECONDS', 45, 0)


def settings():
    secret = os.getenv('FITBAUS_SESSION_SECRET', '').strip()
    secret_file = os.getenv('FITBAUS_SESSION_SECRET_FILE', '').strip()
    if not secret and secret_file:
        secret = Path(secret_file).read_text().strip()
        if len(secret) < 32:
            raise RuntimeError('Session secret file must contain at least 32 characters')
    password = os.getenv('FITBAUS_ADMIN_PASSWORD', '').strip()
    password_hash = os.getenv('FITBAUS_ADMIN_PASSWORD_HASH', '').strip()
    public = os.getenv('FITBAUS_PUBLIC_PROFILES', '*').strip()
    return {
        'SECRET_KEY': secret or secrets.token_hex(32),
        'ADMIN_PASSWORD': password,
        'ADMIN_PASSWORD_HASH': password_hash,
        'DATA_ACCESS_MODE': os.getenv('FITBAUS_DATA_ACCESS', 'private').strip().lower(),
        'PUBLIC_PROFILE_IDS': None if public == '*' else frozenset(p.strip() for p in public.split(',') if p.strip()),
        'SESSION_COOKIE_NAME': 'fitbaus_admin_session',
        'SESSION_COOKIE_HTTPONLY': True,
        'SESSION_COOKIE_SAMESITE': 'Lax',
        'SESSION_COOKIE_SECURE': env_flag('FITBAUS_SESSION_COOKIE_SECURE', True),
        'PERMANENT_SESSION_LIFETIME': timedelta(hours=12),
        'MAX_CONTENT_LENGTH': 64 * 1024,
        'OWNER_PROFILE_ID': validate_profile_id(os.getenv('FITFLARE_PROFILE_ID', 'me').strip()),
        'PROFILES_DIR': str(Path(ROOT_DIR) / 'profiles'),
    }
