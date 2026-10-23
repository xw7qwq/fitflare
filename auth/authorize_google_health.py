"""Authorize or migrate the personal account using Google OAuth with state and PKCE."""
import argparse
from getpass import getpass
import os
import sys
import webbrowser

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common.google_health_auth import (
    DEFAULT_REDIRECT_URI,
    GoogleHealthAuthError,
    begin_google_authorization,
    complete_google_authorization,
    save_google_credentials,
)
from common.profile_paths import resolve_or_prompt_profile


def main():
    parser = argparse.ArgumentParser(description="Connect the Fitflare account to Google Health")
    parser.add_argument("--profile", help="Explicit local directory override for maintenance only")
    parser.add_argument("--configure", action="store_true", help="Prompt for separate Google Cloud OAuth credentials")
    parser.add_argument("--no-browser", action="store_true", help="Only print the Google authorization URL")
    args = parser.parse_args()
    try:
        profile_id = resolve_or_prompt_profile(args.profile)
        if args.configure:
            client_id = input("Google OAuth client ID: ").strip()
            secret = getpass("Google OAuth client secret (hidden): ").strip()
            redirect = input(f"Registered redirect URI [{DEFAULT_REDIRECT_URI}]: ").strip() or DEFAULT_REDIRECT_URI
            save_google_credentials(profile_id, client_id, secret, redirect)
        flow = begin_google_authorization(profile_id)
        print("Allow read access for activity, health metrics, sleep, profile, devices/settings and nutrition to update the dashboard.")
        print("Open this URL in your browser:\n" + flow["auth_url"])
        if not args.no_browser:
            webbrowser.open(flow["auth_url"])
        print("After consent, copy the full redirected URL, including code and state.")
        redirected = getpass("Full redirect URL (hidden): ").strip()
        result = complete_google_authorization(profile_id, redirected)
        print(result["message"])
        if result.get("warning"):
            print(result["warning"])
        return 0
    except (GoogleHealthAuthError, OSError, RuntimeError) as exc:
        print(f"Authorization failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
