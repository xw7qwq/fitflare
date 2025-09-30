"""
Advanced Fitbit OAuth Authorization with Local Web Server
This version uses a local web server to automatically capture the redirect,
making the process even more seamless.
"""

import base64
import requests
import webbrowser
import threading
import time
import sys
import subprocess
import os
import re
import argparse
import getpass
import ssl
import builtins as _builtins
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlencode, urlparse, parse_qs
import json
import os
from tempfile import NamedTemporaryFile

# Ensure console-safe ASCII output regardless of terminal encoding
def _safe_print(*args, **kwargs):
    sep = kwargs.pop('sep', ' ')
    end = kwargs.pop('end', '\n')
    file = kwargs.pop('file', sys.stdout)
    flush = kwargs.pop('flush', False)
    text = sep.join(str(a) for a in args)
    try:
        text.encode('ascii')
    except Exception:
        try:
            text = text.encode('ascii', 'ignore').decode('ascii')
        except Exception:
            pass
    _builtins.print(text, end=end, file=file, flush=flush)

print = _safe_print  # type: ignore

def _atomic_write(path, data):
    """Atomically write data to a file to prevent race conditions"""
    d = os.path.dirname(os.path.abspath(path)) or "."
    with NamedTemporaryFile("w", dir=d, delete=False) as tmp:
        tmp.write(data)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp_path = tmp.name
    os.replace(tmp_path, path)

# Import helper for profile-scoped storage
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common.profile_paths import (
    get_active_profile,
    tokens_file_for,
    ensure_dirs_for_tokens,
    client_credentials_file_for,
)

# Credentials are loaded dynamically - no hardcoded values for security
REDIRECT_URI = "https://localhost:8080/callback"

class CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith('/callback'):
            # Extract code from URL
            parsed = urlparse(self.path)
            query_params = parse_qs(parsed.query)
            code = query_params.get('code', [None])[0]
            
            if code:
                # Store the code for the main thread
                self.server.auth_code = code
                
                # Send success response to browser
                self.send_response(200)
                self.send_header('Content-type', 'text/html')
                self.end_headers()
                
                html = """
                <!DOCTYPE html>
                <html>
                <head>
                    <title>Fitbit Authorization Success</title>
                    <style>
                        body { font-family: Arial, sans-serif; text-align: center; padding: 50px; }
                        .success { color: #4CAF50; font-size: 24px; }
                        .info { color: #666; margin-top: 20px; }
                    </style>
                </head>
                <body>
                    <div class="success">✅ Authorization Successful!</div>
                    <div class="info">You can close this window and return to the terminal.</div>
                </body>
                </html>
                """
                self.wfile.write(html.encode())
            else:
                # Send error response
                self.send_response(400)
                self.send_header('Content-type', 'text/html')
                self.end_headers()
                self.wfile.write(b"<h1>Authorization Error</h1><p>No authorization code received.</p>")
        else:
            self.send_response(404)
            self.end_headers()
    
    def log_message(self, format, *args):
        # Suppress default logging
        pass

def start_callback_server(use_https: bool = False, certfile: str | None = None, keyfile: str | None = None):
    """Start a local web server to capture the OAuth callback.

    If use_https is True, wraps the socket with TLS using the provided cert/key.
    """
    server = HTTPServer(('localhost', 8080), CallbackHandler)
    # Auto-detect cert/key from env if not explicitly provided
    if not certfile and not keyfile:
        env_cert = os.getenv("FITBIT_SSL_CERT", "").strip()
        env_key = os.getenv("FITBIT_SSL_KEY", "").strip()
        if env_cert and env_key and os.path.exists(env_cert) and os.path.exists(env_key):
            use_https = True
            certfile, keyfile = env_cert, env_key
    if use_https:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        if not certfile or not keyfile:
            raise RuntimeError("HTTPS requested but certfile/keyfile not provided")
        context.load_cert_chain(certfile=certfile, keyfile=keyfile)
        server.socket = context.wrap_socket(server.socket, server_side=True)
    server.auth_code = None
    server.timeout = 0.5  # Short timeout for non-blocking
    
    # Run server in a separate thread
    def run_server():
        while server.auth_code is None:
            server.handle_request()
    
    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()
    return server


def _find_repeating_segment(s: str, min_seg: int = 16) -> int | None:
    """Return smallest repeating segment length if s is k>=2 repeats, else None."""
    n = len(s)
    for seg in range(min_seg, (n // 2) + 1):
        if n % seg == 0 and s == s[:seg] * (n // seg):
            return seg
    return None


def _sanitize_credential(raw: str, label: str) -> str:
    v = raw
    if v != v.strip():
        print(f"Note: trimmed whitespace from {label} input.")
        v = v.strip()
    # Remove any internal whitespace
    v_no_ws = re.sub(r"\s+", "", v)
    if v_no_ws != v:
        print(f"Note: removed internal whitespace from {label} input.")
        v = v_no_ws
    rep = _find_repeating_segment(v, min_seg=16)
    if rep and rep < len(v):
        print(f"Note: detected repeated pattern in {label}; using the first segment.")
        v = v[:rep]
    return v


def _load_or_prompt_credentials(profile_id: str | None, reenter: bool = False):
    """Load Fitbit client credentials from env, profile file, or prompt and save.

    Order of precedence:
    1) FITBIT_CLIENT_ID and FITBIT_CLIENT_SECRET environment variables
    2) profiles/<id>/auth/client.json (or auth/client.json)
    3) Prompt the user and save to the profile's client.json
    """
    env_id = os.getenv("FITBIT_CLIENT_ID", "")
    env_secret = os.getenv("FITBIT_CLIENT_SECRET", "")
    if env_id and env_secret:
        cid = _sanitize_credential(env_id, "Client ID")
        csec = _sanitize_credential(env_secret, "Client Secret")
        return cid, csec, "env", client_credentials_file_for(profile_id)

    path = client_credentials_file_for(profile_id)
    try:
        if (not reenter) and os.path.exists(path):
            # Check if file is empty or blank
            if os.path.getsize(path) == 0:
                print(f"Client credentials file is empty: {path}")
                return None, None, "empty", path
            
            with open(path, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if not content:
                    print(f"Client credentials file is blank: {path}")
                    return None, None, "blank", path
                
                try:
                    data = json.loads(content)
                except json.JSONDecodeError:
                    print(f"Client credentials file is not valid JSON: {path}")
                    return None, None, "invalid_json", path
            
            # Check if data is empty dict
            if not data or not isinstance(data, dict):
                print(f"Client credentials file contains no data: {path}")
                return None, None, "no_data", path
            
            cid_raw = str(data.get("client_id", ""))
            csec_raw = str(data.get("client_secret", ""))
            
            # Check if credentials are empty
            if not cid_raw or not csec_raw:
                print(f"Client credentials file missing required fields: {path}")
                return None, None, "missing_fields", path
            
            cid = _sanitize_credential(cid_raw, "Client ID")
            csec = _sanitize_credential(csec_raw, "Client Secret")
            if cid and csec:
                if cid != cid_raw or csec != csec_raw:
                    try:
                        with open(path, "w", encoding="utf-8") as f:
                            json.dump({
                                "client_id": cid, 
                                "client_secret": csec,
                                "created_at": datetime.now().isoformat()
                            }, f)
                        print(f"Normalized credentials written to: {path}")
                    except Exception as e:
                        print(f"Warning: could not rewrite normalized credentials: {e}")
                return cid, csec, "file", path
    except Exception as e:
        print(f"Warning: could not read client credentials file: {e}")
        pass

    # Prompt user
    print("\nProvide your Fitbit app credentials for this profile.")
    cid_raw = input("Client ID: ")
    cid = _sanitize_credential(cid_raw, "Client ID")
    csec_raw = input("Client Secret: ")
    csec = _sanitize_credential(csec_raw, "Client Secret")
    if not cid or not csec:
        raise RuntimeError("Client ID and Client Secret are required.")

    # Save for future use
    try:
        ensure_dirs_for_tokens(path)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({
                "client_id": cid, 
                "client_secret": csec,
                "created_at": datetime.now().isoformat()
            }, f)
        print(f"Saved client credentials to: {path}")
    except Exception as e:
        print(f"Warning: could not save client credentials: {e}")
    return cid, csec, "prompt", path

def copy_to_clipboard(text: str) -> bool:
    """Best-effort copy to clipboard across platforms without hard deps."""
    try:
        import pyperclip  # type: ignore
        pyperclip.copy(text)
        print("📋 Authorization code copied to clipboard.")
        return True
    except Exception:
        pass
    try:
        if sys.platform.startswith("win"):
            p = subprocess.Popen(["clip"], stdin=subprocess.PIPE, text=True)
            p.communicate(text)
            if p.returncode == 0:
                print("📋 Authorization code copied to clipboard.")
                return True
        elif sys.platform == "darwin":
            p = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE, text=True)
            p.communicate(text)
            if p.returncode == 0:
                print("📋 Authorization code copied to clipboard.")
                return True
        else:
            for cmd in (["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "-b", "-i"]):
                try:
                    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, text=True)
                    p.communicate(text)
                    if p.returncode == 0:
                        print("📋 Authorization code copied to clipboard.")
                        return True
                except Exception:
                    continue
    except Exception:
        pass
    print("ℹ️ Could not copy to clipboard automatically. Proceeding without it.")
    return False

def extract_code_from_url(url):
    """Extract authorization code from redirect URL"""
    try:
        parsed = urlparse(url)
        query_params = parse_qs(parsed.query)
        return query_params.get('code', [None])[0]
    except Exception:
        return None

def get_auth_code_manual(redirect_uri: str, client_id: str):
    """Manual flow: open browser and prompt user to paste redirected URL."""
    print("🔐 Fitbit OAuth Authorization (Manual Fallback)")
    print("=" * 50)
    params = {
        "client_id": client_id,
        "response_type": "code",
        "scope": "heartrate sleep activity profile",
        "redirect_uri": redirect_uri,
    }
    auth_url = f"https://www.fitbit.com/oauth2/authorize?{urlencode(params)}"
    print("🔗 Opening browser for Fitbit login...")
    webbrowser.open(auth_url)
    print(f"\n🌐 Authorization URL:")
    print(f"   {auth_url}")
    print("\n📋 Instructions:")
    print("1. Complete the login process in your browser")
    print("2. You'll be redirected to a URL that looks like:")
    print(f"   {redirect_uri}?code=XXXXX")
    print("3. Copy the ENTIRE redirected URL and paste it below")
    print("\n" + "=" * 50)
    while True:
        user_input = input("\nPaste the complete redirected URL here: ").strip()
        if not user_input:
            print("❌ Please enter a URL")
            continue
        auth_code = extract_code_from_url(user_input)
        if auth_code:
            print(f"✅ Successfully extracted authorization code: {auth_code[:10]}...")
            copy_to_clipboard(auth_code)
            return auth_code, redirect_uri
        else:
            print("❌ Could not extract authorization code from URL")
            print("   Make sure you copied the complete URL including the '?code=' part")
            manual_code = input("   Or paste just the code part: ").strip()
            if manual_code and re.match(r'^[a-f0-9]+$', manual_code, re.IGNORECASE):
                copy_to_clipboard(manual_code)
                return manual_code, redirect_uri
            else:
                print("   Invalid code format. Please try again.")

def get_auth_code_advanced(client_id: str):
    """Get authorization code using local server, with manual fallback.

    Honors FITBIT_REDIRECT_URI if set. If redirect is not local, uses manual flow.
    """
    redirect_uri = os.getenv("FITBIT_REDIRECT_URI", REDIRECT_URI).strip()
    is_local = (
        redirect_uri.startswith("http://localhost:")
        or redirect_uri.startswith("http://127.0.0.1:")
        or redirect_uri.startswith("https://localhost:")
        or redirect_uri.startswith("https://127.0.0.1:")
    )
    if not is_local:
        return get_auth_code_manual(redirect_uri, client_id)

    # If HTTPS localhost redirect is configured but SSL cert/key are missing,
    # fall back to the manual flow immediately to avoid timeouts.
    if redirect_uri.startswith("https://localhost:") or redirect_uri.startswith("https://127.0.0.1:"):
        cert = os.getenv("FITBIT_SSL_CERT", "").strip()
        key = os.getenv("FITBIT_SSL_KEY", "").strip()
        if not (cert and key and os.path.exists(cert) and os.path.exists(key)):
            print("HTTPS redirect configured, but FITBIT_SSL_CERT/KEY not set or files missing.")
            print("Switching to manual flow using your configured redirect URI.")
            return get_auth_code_manual(redirect_uri, client_id)

    print("🔐 Fitbit OAuth Authorization (Advanced)")
    print("=" * 50)

    print("🌐 Starting local web server to capture callback...")
    server = start_callback_server()

    params = {
        "client_id": client_id,
        "response_type": "code",
        "scope": "heartrate sleep activity profile",
        "redirect_uri": redirect_uri,
    }
    auth_url = f"https://www.fitbit.com/oauth2/authorize?{urlencode(params)}"

    print("🔗 Opening browser for Fitbit login...")
    webbrowser.open(auth_url)

    print(f"\n🌐 Authorization URL:")
    print(f"   {auth_url}")
    print("\n📋 Instructions:")
    print("1. Complete the login process in your browser")
    print("2. You'll be automatically redirected back to this application")
    print("3. The authorization code will be captured automatically")
    print("\n⏳ Waiting for authorization...")

    timeout = int(os.getenv("FITBIT_AUTH_TIMEOUT", "60"))
    start_time = time.time()
    while server.auth_code is None:
        if time.time() - start_time > timeout:
            print("\n⚠️  No callback captured within timeout.")
            # Fallback to manual flow using the same redirect URI (or an override)
            fallback_redirect = os.getenv("FITBIT_FALLBACK_REDIRECT", redirect_uri).strip()
            print("Switching to manual flow using your configured redirect URI.")
            return get_auth_code_manual(fallback_redirect, client_id)
        time.sleep(1)
        print(".", end="", flush=True)

    print(f"\n✅ Authorization code received: {server.auth_code[:10]}...")
    copy_to_clipboard(server.auth_code)
    return (server.auth_code, redirect_uri)

def update_profiles_index(profile_id):
    """Update profiles/index.json to include the new profile"""
    if not profile_id:
        return  # Skip for default profile
    
    try:
        # Get the profiles directory path
        profiles_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "profiles")
        index_file = os.path.join(profiles_dir, "index.json")
        
        # Ensure profiles directory exists
        os.makedirs(profiles_dir, exist_ok=True)
        
        # Read existing profiles or create empty list
        existing_profiles = []
        if os.path.exists(index_file):
            try:
                with open(index_file, "r", encoding="utf-8") as f:
                    existing_profiles = json.load(f)
                if not isinstance(existing_profiles, list):
                    existing_profiles = []
            except (json.JSONDecodeError, Exception):
                existing_profiles = []
        
        # Add new profile if not already present
        if profile_id not in existing_profiles:
            existing_profiles.append(profile_id)
            existing_profiles.sort()  # Keep sorted for consistency
            
            # Write updated profiles list
            with open(index_file, "w", encoding="utf-8") as f:
                json.dump(existing_profiles, f, indent=2)
            
            print(f"📝 Added profile '{profile_id}' to profiles/index.json")
        else:
            print(f"ℹ️ Profile '{profile_id}' already exists in profiles/index.json")
            
    except Exception as e:
        print(f"⚠️ Warning: Could not update profiles/index.json: {e}")

def exchange_code_for_token(auth_code, redirect_uri, client_id, client_secret, profile_id=None):
    """Exchange authorization code for access token"""
    print("\n🔄 Exchanging authorization code for access token...")
    
    token_url = "https://api.fitbit.com/oauth2/token"
    auth_header = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    headers = {
        "Authorization": f"Basic {auth_header}",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    data = {
        "grant_type": "authorization_code",
        "code": auth_code,
        "redirect_uri": redirect_uri,
        "client_id": client_id
    }
    
    try:
        res = requests.post(token_url, headers=headers, data=data, timeout=30)
        
        if res.status_code == 200:
            # Save tokens to profile-scoped location (or legacy default)
            tokens_path = tokens_file_for(get_active_profile(profile_id))
            ensure_dirs_for_tokens(tokens_path)
            # Use atomic write to prevent race conditions with fetch process
            _atomic_write(tokens_path, res.text)
            
            # Update profiles index for new profiles
            update_profiles_index(profile_id)
            
            # Parse and display token info
            token_data = res.json()
            print("✅ Successfully obtained access token!")
            print(f"📊 Token expires in: {token_data.get('expires_in', 'Unknown')} seconds")
            print(f"🔄 Refresh token: {token_data.get('refresh_token', 'N/A')[:20]}...")
            print(f"💾 Tokens saved to: {tokens_path}")
            print("\n🎉 You can now run the fetch scripts to get your Fitbit data!")
            return True
            
        else:
            print(f"❌ Error fetching token: {res.status_code}")
            print(f"Response: {res.text}")
            return False
            
    except Exception as e:
        print(f"❌ Error: {e}")
        return False

def sync_existing_profiles():
    """Sync existing profiles to index.json"""
    try:
        profiles_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "profiles")
        index_file = os.path.join(profiles_dir, "index.json")
        
        if not os.path.exists(profiles_dir):
            return
        
        # Find all existing profile directories
        existing_profiles = []
        for item in os.listdir(profiles_dir):
            profile_path = os.path.join(profiles_dir, item)
            if os.path.isdir(profile_path) and not item.startswith('.'):
                # Check if it has auth/tokens.json
                tokens_file = os.path.join(profile_path, "auth", "tokens.json")
                if os.path.exists(tokens_file):
                    existing_profiles.append(item)
        
        existing_profiles.sort()
        
        # Read current index or create empty list
        current_profiles = []
        if os.path.exists(index_file):
            try:
                with open(index_file, "r", encoding="utf-8") as f:
                    current_profiles = json.load(f)
                if not isinstance(current_profiles, list):
                    current_profiles = []
            except (json.JSONDecodeError, Exception):
                current_profiles = []
        
        # Update if there are differences
        if set(existing_profiles) != set(current_profiles):
            with open(index_file, "w", encoding="utf-8") as f:
                json.dump(existing_profiles, f, indent=2)
            print(f"🔄 Synced {len(existing_profiles)} profiles to index.json")
        
    except Exception as e:
        print(f"⚠️ Warning: Could not sync profiles to index.json: {e}")

def main():
    """Main authorization flow"""
    try:
        parser = argparse.ArgumentParser(description="Fitbit authorization (advanced)")
        parser.add_argument("--profile", help="Profile id to save tokens under", default=None)
        parser.add_argument("--verbose", action="store_true", help="Print selected credentials and paths")
        parser.add_argument("--reenter", action="store_true", help="Re-enter and overwrite saved client credentials for this profile")
        parser.add_argument("--sync-profiles", action="store_true", help="Sync existing profiles to index.json")
        args = parser.parse_args()
        
        # Show usage guidance if no profile specified
        if args.profile is None and not args.sync_profiles:
            print()
            print("Correct usage:")
            print()
            print("  python authorize_fitbit.py --profile [your_name]")
            print()
            print("Example:")
            print()
            print("  python authorize_fitbit.py --profile john")
            print()
            print("This will create a personal profile for your Fitbit data.")
            print("You can also use --help to see all available options.")
            print()
            print("=" * 60)
            print()
            print("Continuing with default profile (single-user setup)...")
            print("(Press Ctrl+C to cancel and use --profile instead)")
            print()
            try:
                response = input("Do you want to continue with default profile? (y/n): ").strip().lower()
                if response in ['n', 'no']:
                    print("Cancelled. Please run with --profile [your_name] for the best experience.")
                    return
                elif response not in ['y', 'yes']:
                    print("Invalid response. Please run with --profile [your_name] for the best experience.")
                    return
            except KeyboardInterrupt:
                print("\nCancelled. Please run with --profile [your_name] for the best experience.")
                return
            
            args.profile = "default"
        
        # Sync existing profiles if requested
        if args.sync_profiles:
            sync_existing_profiles()
            return

        # Safety check: prevent proceeding if default profile already exists
        # Applies both when user explicitly passes --profile default or chooses default interactively
        if str(args.profile).strip().lower() == "default":
            root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            default_profile_dir = os.path.join(root_dir, "profiles", "default")
            default_tokens_path = tokens_file_for("default")
            default_client_path = client_credentials_file_for("default")
            if os.path.isdir(default_profile_dir) or os.path.exists(default_tokens_path) or os.path.exists(default_client_path):
                print("\n⚠️  A default profile already exists. Aborting to avoid overwriting it.")
                print("   Tip: Use --profile <your_name> to create a new profile, or remove the existing default profile if you intend to recreate it.")
                return

        # Load or prompt client credentials
        cid, csec, cred_source, cred_path = _load_or_prompt_credentials(args.profile, reenter=args.reenter)
        
        # Validate credentials were loaded successfully
        if not cid or not csec:
            print("❌ Error: Could not load valid Fitbit credentials.")
            print("   Please ensure you have:")
            print("   1. Set FITBIT_CLIENT_ID and FITBIT_CLIENT_SECRET environment variables, OR")
            print("   2. Created a client.json file in your profile directory, OR")
            print("   3. Run the script to be prompted for credentials")
            print(f"   Expected location: {cred_path}")
            return

        if args.verbose:
            profile_eff = get_active_profile(args.profile)
            tokens_path = tokens_file_for(profile_eff)
            redirect_eff = os.getenv("FITBIT_REDIRECT_URI", REDIRECT_URI).strip()
            print("--- Authorization Context ---")
            print(f"Profile: {profile_eff or '(default)'}")
            print(f"Credentials source: {cred_source} ({cred_path})")
            print(f"Client ID: {cid}")
            print(f"Redirect URI: {redirect_eff}")
            print(f"Will save tokens to: {tokens_path}")
            print("-----------------------------")

        # Get authorization code (and redirect used)
        result = get_auth_code_advanced(cid)
        
        if result:
            auth_code, used_redirect = result
            # Exchange for token
            success = exchange_code_for_token(auth_code, used_redirect, cid, csec, profile_id=args.profile)
            if success:
                print("\n🎯 Authorization complete! You're ready to fetch data.")
            else:
                print("\n❌ Authorization failed. Please try again.")
        else:
            print("\n❌ No authorization code received.")
            
    except KeyboardInterrupt:
        print("\n\n⏹️ Authorization cancelled by user.")
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")

if __name__ == "__main__":
    main()

