import os
import json
import base64
import time
import requests
from tempfile import NamedTemporaryFile
import sys

# Allow importing helper from repository root
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common.profile_paths import (
    get_active_profile,
    tokens_file_for,
    ensure_dirs_for_tokens,
    client_credentials_file_for,
)

TIMEOUT = 30
MAX_RETRIES = 4
BACKOFF_BASE = 2.0

def _mask(s):
    if not s:
        return ""
    if len(s) <= 6:
        return "*" * len(s)
    return s[:3] + "…" + s[-3:]

def _atomic_write(path, data):
    d = os.path.dirname(os.path.abspath(path)) or "."
    with NamedTemporaryFile("w", dir=d, delete=False) as tmp:
        json.dump(data, tmp)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp_path = tmp.name
    os.replace(tmp_path, path)

def _load_tokens(path):
    if not os.path.exists(path):
        raise FileNotFoundError(f"Token file not found: {path}")
    
    # Check if file is empty or blank
    if os.path.getsize(path) == 0:
        raise ValueError(f"Token file is empty: {path}")
    
    with open(path) as f:
        content = f.read().strip()
        if not content:
            raise ValueError(f"Token file is blank: {path}")
        
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            raise ValueError(f"Token file is not valid JSON: {path}")
    
    # Check if data is empty dict
    if not data or not isinstance(data, dict):
        raise ValueError(f"Token file contains no data: {path}")
    
    if "refresh_token" not in data:
        raise KeyError("Token file missing 'refresh_token'")
    
    if not data.get("refresh_token"):
        raise ValueError("Token file contains empty 'refresh_token'")
    
    return data

def _resolve_tokens_file() -> str:
    """Resolve the tokens file path from env/CLI profile with legacy fallback."""
    override = os.getenv("FITBIT_TOKENS_FILE", "").strip()
    if override:
        return os.path.abspath(override)
    profile_id = get_active_profile(None)
    return tokens_file_for(profile_id)


def _resolve_client_credentials() -> tuple[str, str]:
    """Resolve client ID/secret from env or profile file.

    Production refreshes should use the app credentials that belong to the
    current profile (or explicit env vars), instead of silently falling back to
    a hardcoded shared developer app.
    """
    def _find_repeating_segment(s: str, min_seg: int = 16) -> int | None:
        n = len(s)
        for seg in range(min_seg, (n // 2) + 1):
            if n % seg == 0 and s == s[:seg] * (n // seg):
                return seg
        return None

    def _sanitize(v: str, label: str) -> str:
        orig = v
        v = v.strip()
        # Remove internal whitespace
        import re as _re
        v2 = _re.sub(r"\s+", "", v)
        if v2 != v:
            print(f"[fitbit] Note: removed whitespace from {label}.")
            v = v2
        rep = _find_repeating_segment(v, min_seg=16)
        if rep and rep < len(v):
            print(f"[fitbit] Note: detected repeated pattern in {label}; using first segment.")
            v = v[:rep]
        return v

    env_id = os.getenv("FITBIT_CLIENT_ID", "")
    env_secret = os.getenv("FITBIT_CLIENT_SECRET", "")
    if env_id and env_secret:
        return _sanitize(env_id, "Client ID"), _sanitize(env_secret, "Client Secret")
    profile_id = get_active_profile(None)
    cred_path = client_credentials_file_for(profile_id)
    try:
        if os.path.exists(cred_path):
            with open(cred_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            cid_raw = str(data.get("client_id", ""))
            csec_raw = str(data.get("client_secret", ""))
            cid = _sanitize(cid_raw, "Client ID")
            csec = _sanitize(csec_raw, "Client Secret")
            if cid and csec:
                if cid != cid_raw or csec != csec_raw:
                    try:
                        with open(cred_path, "w", encoding="utf-8") as f:
                            json.dump({"client_id": cid, "client_secret": csec}, f)
                        print(f"[fitbit] Normalized credentials written to: {cred_path}")
                    except Exception as e:
                        print(f"[fitbit] Warning: could not rewrite normalized credentials: {e}")
                return cid, csec
    except Exception:
        pass

    raise RuntimeError(
        "[fitbit] Client credentials not found. Set FITBIT_CLIENT_ID and "
        "FITBIT_CLIENT_SECRET, or make sure profiles/<profile>/auth/client.json exists."
    )


def refresh_token():
    tokens_file = _resolve_tokens_file()
    print(f"[fitbit] Using token file: {os.path.abspath(tokens_file)}")
    tokens = _load_tokens(tokens_file)
    rt = tokens.get("refresh_token")
    print(f"[fitbit] Refresh token: {_mask(rt)}")

    token_url = "https://api.fitbit.com/oauth2/token"
    cid, csec = _resolve_client_credentials()
    auth_header = base64.b64encode(f"{cid}:{csec}".encode()).decode()
    headers = {
        "Authorization": f"Basic {auth_header}",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    data = {
        "grant_type": "refresh_token",
        "refresh_token": rt
    }

    attempt = 0
    while attempt < MAX_RETRIES:
        attempt += 1
        try:
            print(f"[fitbit] Requesting new access token (attempt {attempt}/{MAX_RETRIES})")
            res = requests.post(token_url, headers=headers, data=data, timeout=TIMEOUT)
        except requests.Timeout:
            if attempt >= MAX_RETRIES:
                raise TimeoutError("[fitbit] Fitbit token endpoint timed out repeatedly")
            delay = BACKOFF_BASE ** attempt
            print(f"[fitbit] Timeout. Retrying in {delay:.1f}s")
            time.sleep(delay)
            continue
        except requests.RequestException as e:
            if attempt >= MAX_RETRIES:
                raise RuntimeError(f"[fitbit] Network error refreshing token: {e}")
            delay = BACKOFF_BASE ** attempt
            print(f"[fitbit] Network error: {e}. Retrying in {delay:.1f}s")
            time.sleep(delay)
            continue

        if res.status_code == 200:
            try:
                new_tokens = res.json()
            except ValueError:
                raise RuntimeError("[fitbit] Response was not JSON")
            at = new_tokens.get("access_token")
            new_rt = new_tokens.get("refresh_token")
            if not at or not new_rt:
                raise RuntimeError("[fitbit] Missing fields in token response")
            backup_path = tokens_file + ".bak"
            try:
                if os.path.exists(tokens_file):
                    with open(tokens_file, "rb") as src, open(backup_path, "wb") as dst:
                        dst.write(src.read())
                    print(f"[fitbit] Backed up existing tokens to {backup_path}")
            except Exception as e:
                print(f"[fitbit] Warning: failed to create backup: {e}")
            ensure_dirs_for_tokens(tokens_file)
            _atomic_write(tokens_file, new_tokens)
            print(f"[fitbit] Token refresh OK. Access: {_mask(at)}, Refresh: {_mask(new_rt)}")
            return at

        if res.status_code == 429:
            retry_after = res.headers.get("Retry-After")
            try:
                delay = max(1, int(retry_after)) if retry_after is not None else int(BACKOFF_BASE ** attempt)
            except ValueError:
                delay = int(BACKOFF_BASE ** attempt)
            print(f"[fitbit] Rate limited (429). Waiting {delay}s")
            time.sleep(delay)
            continue

        if res.status_code in (500, 502, 503, 504):
            if attempt >= MAX_RETRIES:
                raise RuntimeError(f"[fitbit] Server error {res.status_code}: {res.text}")
            delay = int(BACKOFF_BASE ** attempt)
            print(f"[fitbit] Server error {res.status_code}. Retrying in {delay}s")
            time.sleep(delay)
            continue

        if res.status_code in (400, 401):
            try:
                err = res.json()
            except ValueError:
                err = {"error": res.text}
            err_type = str(err)
            if "invalid_grant" in err_type or "invalid_token" in err_type:
                raise PermissionError("[fitbit] Refresh token is invalid or expired. Re-authorize the app.")
            raise RuntimeError(f"[fitbit] Auth error {res.status_code}: {err}")
        try:
            detail = res.text
        except Exception:
            detail = "<no body>"
        raise RuntimeError(f"[fitbit] Unexpected status {res.status_code}: {detail}")

    raise RuntimeError("[fitbit] Exhausted retries without obtaining a token")

if __name__ == "__main__":
    try:
        access_token = refresh_token()
        print(f"[fitbit] Successfully refreshed token: {_mask(access_token)}")
    except Exception as e:
        print(f"[fitbit] Error: {e}")
        exit(1)
