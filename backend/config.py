"""Environment configuration, with no application or scheduler side effects."""
import os
from pathlib import Path
from common.profile_paths import ROOT_DIR, validate_profile_id


def env_flag(name, default):
    return os.getenv(name, str(default)).strip().lower() not in {'0', 'false', 'no', 'off'}


def env_int(name, default, minimum):
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except ValueError:
        return default


AUTO_SYNC_ENABLED = env_flag('FITFLARE_AUTO_SYNC_ENABLED', True)
AUTO_SYNC_INTERVAL_SECONDS = env_int('FITFLARE_AUTO_SYNC_INTERVAL_SECONDS', 21600, 60)
AUTO_SYNC_SCAN_INTERVAL_SECONDS = env_int('FITFLARE_AUTO_SYNC_SCAN_INTERVAL_SECONDS', 300, 30)
AUTO_SYNC_STARTUP_DELAY_SECONDS = env_int('FITFLARE_AUTO_SYNC_STARTUP_DELAY_SECONDS', 45, 0)


def settings():
    return {
        # Require deliberate publication when upgrading an older private install.
        'DATA_ACCESS_MODE': os.getenv('FITFLARE_DATA_ACCESS', '').strip().lower(),
        'MAX_CONTENT_LENGTH': 64 * 1024,
        'OWNER_PROFILE_ID': validate_profile_id(os.getenv('FITFLARE_PROFILE_ID', 'me').strip()),
        'PROFILES_DIR': str(Path(ROOT_DIR) / 'profiles'),
    }
