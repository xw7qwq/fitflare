import os
import sys
import argparse
import subprocess
from datetime import datetime

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common.dashboard_cache import build_dashboard_cache
from common.profile_paths import get_active_profile


def run_script(script_path: str, cwd: str, extra_args=None) -> tuple[int, str | None]:
    print("=" * 60)
    print(f"Starting {os.path.basename(script_path)} at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    try:
        # Stream child output and capture a concise start line for summary
        cmd = [sys.executable, "-u", script_path]
        if extra_args:
            cmd.extend(extra_args)
        env = os.environ.copy()
        # Ensure child scripts can emit Unicode without crashing on Windows consoles
        env.setdefault("PYTHONIOENCODING", "utf-8")
        # Force unbuffered output for real-time streaming
        env.setdefault("PYTHONUNBUFFERED", "1")
        out_enc = sys.stdout.encoding or "utf-8"
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=0,  # Unbuffered for real-time output
            universal_newlines=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
        start_line: str | None = None
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                # Echo child output live (transcode safely for current console)
                try:
                    sys.stdout.buffer.write(line.encode(out_enc, errors="replace"))
                    sys.stdout.buffer.flush()
                except Exception:
                    # Fallback to plain print if buffer not available
                    try:
                        print(line, end="", flush=True)
                    except Exception:
                        pass
                # Force flush to ensure real-time output to parent process
                sys.stdout.flush()
                # Capture the first "Starting ... fetch from ..." line for summary
                if start_line is None:
                    l = line.strip()
                    l_low = l.lower()
                    if "starting " in l_low and " fetch from " in l_low:
                        start_line = l
        except KeyboardInterrupt:
            proc.kill()
            raise
        code = proc.wait()
        if code == 0:
            print(f"OK: {os.path.basename(script_path)} completed successfully.\n")
        else:
            print(f"ERR: {os.path.basename(script_path)} exited with code {code}.\n")
        sys.stdout.flush()  # Ensure output is flushed to parent process
        return code, start_line
    except KeyboardInterrupt:
        print("Interrupted: Received KeyboardInterrupt; stopping.")
        return 130, None
    except Exception as e:
        print(f"ERR: Failed to run {script_path}: {e}")
        return 1, None



def main():
    parser = argparse.ArgumentParser(description="Sync the configured personal Fitbit account.")
    parser.add_argument("--stop-on-error", action="store_true", help="Stop after a failed fetch.")
    parser.add_argument("--profile", default=None, help="Explicit local directory override for maintenance only.")
    args = parser.parse_args()
    profile = get_active_profile(args.profile)
    this_dir = os.path.dirname(os.path.abspath(__file__))
    scripts = ["fetch_steps.py", "fetch_rhr_data.py", "fetch_hrv_data.py",
               "fetch_sleep_data.py", "fetch_profile_snapshot.py"]
    results = []
    print(f"Syncing personal account: {profile}")
    for index, name in enumerate(scripts, 1):
        print(f"\n[{index}/{len(scripts)}] Starting {name}...")
        path = os.path.join(this_dir, name)
        if not os.path.isfile(path):
            code, detail = 127, "Fetch script missing"
        else:
            code, detail = run_script(path, cwd=this_dir, extra_args=["--profile", profile])
        results.append((name, code, detail))
        if code and args.stop_on_error:
            break
    try:
        build_dashboard_cache(profile)
        results.append(("build_dashboard_cache", 0, "Dashboard cache rebuilt"))
    except Exception as error:
        results.append(("build_dashboard_cache", 1, str(error)))
    print("\nSummary:")
    for name, code, detail in results:
        status = "OK" if code == 0 else f"FAIL ({code})"
        print(f" - {name}: {status}" + (f" | {detail}" if detail else ""))
    sys.stdout.flush()
    return 1 if any(code for _, code, _ in results) else 0


if __name__ == "__main__":
    sys.exit(main())
