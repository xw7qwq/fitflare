"""Background sync, file locks and progress parsing. Importing starts no threads."""
import os
import json
import subprocess
import sys
import threading
import tempfile
import time
import fcntl
from datetime import date, datetime
from collections import deque
from common.profile_paths import list_profiles as list_profile_ids, owner_profile_id, profile_path_for, tokens_file_for
from .config import AUTO_SYNC_ENABLED, AUTO_SYNC_INTERVAL_SECONDS, AUTO_SYNC_SCAN_INTERVAL_SECONDS, AUTO_SYNC_STARTUP_DELAY_SECONDS
from .time_utils import _parse_date, _now_iso
from . import jobs

auto_sync_thread = None
auto_sync_stop_event = threading.Event()

def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is not None:
            return parsed.astimezone().replace(tzinfo=None)
        return parsed
    except Exception:
        return None


def _auto_sync_log(message: str, level: str = "INFO"):
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] [AUTO-SYNC] [{level}] {message}")


def _require_owner(profile_id):
    if profile_id != owner_profile_id():
        raise ValueError('Background tasks can only use the configured personal account')
    for directory in ('auth', 'cache', 'csv'):
        profile_path_for(profile_id, directory)


def _profile_cache_dir(profile_id: str) -> str:
    _require_owner(profile_id)
    return str(profile_path_for(profile_id, 'cache'))


def _ensure_profile_cache_dir(profile_id: str):
    os.makedirs(_profile_cache_dir(profile_id), exist_ok=True)


def _profile_fetch_lock_path(profile_id: str) -> str:
    _require_owner(profile_id)
    return str(profile_path_for(profile_id, "cache", ".fetch.lock"))


def _auto_sync_state_path(profile_id: str) -> str:
    _require_owner(profile_id)
    return str(profile_path_for(profile_id, "cache", "auto_sync_state.json"))


def _dashboard_cache_path(profile_id: str) -> str:
    _require_owner(profile_id)
    return str(profile_path_for(profile_id, "cache", "dashboard.json"))


def _load_json_file(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _write_json_file(path: str, payload: dict):
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix='.sync-', suffix='.tmp', dir=directory)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def _load_auto_sync_state(profile_id: str) -> dict:
    return _load_json_file(_auto_sync_state_path(profile_id))


def _save_auto_sync_state(profile_id: str, **updates):
    state = _load_auto_sync_state(profile_id)
    state.update(updates)
    state["profile"] = profile_id
    state["updated_at"] = _now_iso()
    _write_json_file(_auto_sync_state_path(profile_id), state)


def _dashboard_generated_at(profile_id: str) -> datetime | None:
    payload = _load_json_file(_dashboard_cache_path(profile_id))
    generated_at = payload.get("generated_at")
    parsed = _parse_iso_datetime(generated_at if isinstance(generated_at, str) else None)
    if parsed:
        return parsed
    try:
        cache_path = _dashboard_cache_path(profile_id)
        if os.path.exists(cache_path):
            return datetime.fromtimestamp(os.path.getmtime(cache_path))
    except Exception:
        return None
    return None


def _last_auto_sync_reference(profile_id: str) -> datetime | None:
    state = _load_auto_sync_state(profile_id)
    candidates = [
        _parse_iso_datetime(state.get("last_attempt_at") if isinstance(state.get("last_attempt_at"), str) else None),
        _dashboard_generated_at(profile_id),
    ]
    valid = [candidate for candidate in candidates if candidate is not None]
    return max(valid) if valid else None


def _profile_has_refresh_token(profile_id: str) -> bool:
    _require_owner(profile_id)
    tokens_path = tokens_file_for(profile_id)
    payload = _load_json_file(tokens_path)
    return bool(payload.get("refresh_token"))


def _discover_syncable_profiles() -> list[str]:
    return [profile_id for profile_id in list_profile_ids() if _profile_has_refresh_token(profile_id)]


def _profile_due_for_auto_sync(profile_id: str, now_dt: datetime | None = None) -> bool:
    now_dt = now_dt or datetime.now()
    reference = _last_auto_sync_reference(profile_id)
    if reference is None:
        return True
    return (now_dt - reference).total_seconds() >= AUTO_SYNC_INTERVAL_SECONDS


def _prepare_fetch_env(profile_id: str) -> dict:
    _require_owner(profile_id)
    env = os.environ.copy()
    env["FITFLARE_PROFILE_ID"] = profile_id
    env["FITBIT_PROFILE"] = profile_id
    env["FITBIT_TOKENS_FILE"] = tokens_file_for(profile_id)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    return env


def _acquire_profile_fetch_lock(profile_id: str, owner: str):
    _ensure_profile_cache_dir(profile_id)
    lock_path = _profile_fetch_lock_path(profile_id)
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None

    payload = {
        "profile": profile_id,
        "owner": owner,
        "pid": os.getpid(),
        "acquired_at": _now_iso(),
    }
    os.ftruncate(fd, 0)
    os.write(fd, json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    os.fsync(fd)
    return fd


def _release_profile_fetch_lock(lock_fd):
    if lock_fd is None:
        return
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
    except Exception:
        pass
    try:
        os.close(lock_fd)
    except Exception:
        pass


def _refresh_profile_tokens(profile_id: str, log_prefix: str) -> tuple[bool, str | None]:
    _require_owner(profile_id)
    tokens_file = tokens_file_for(profile_id)
    print(f"[{log_prefix}] Checking tokens file: {tokens_file}")
    if not os.path.exists(tokens_file):
        return False, f'Profile {profile_id} not found. Open Account settings and configure Fitbit'

    try:
        with open(tokens_file, "r", encoding="utf-8") as handle:
            tokens = json.load(handle)
    except Exception as exc:
        return False, f"Error checking tokens: {exc}"

    if not tokens or "refresh_token" not in tokens or not tokens.get("refresh_token"):
        return False, f'Profile {profile_id} needs authorization. Open Account settings and authorize Fitbit'

    refresh_result = subprocess.run(
        [sys.executable, "auth/refresh_token.py"],
        cwd=os.getcwd(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_prepare_fetch_env(profile_id),
        timeout=30,
    )
    print(f"[{log_prefix}] Token refresh completed. Return code: {refresh_result.returncode}")

    if refresh_result.returncode == 0:
        if refresh_result.stdout:
            print(f"[{log_prefix}] {refresh_result.stdout.strip()}")
        return True, None

    error_msg = (refresh_result.stderr or refresh_result.stdout or "Token refresh failed").strip()
    if "[fitbit] Error:" in error_msg:
        error_msg = error_msg.split("[fitbit] Error:")[-1].strip()
    if "Token file not found:" in error_msg:
        error_msg = "Token file not found"
    if "Refresh token is invalid or expired" in error_msg:
        error_msg = "Refresh token is invalid or expired"
    return False, f"Token refresh failed: {error_msg}. Open Account settings and authorize Fitbit"


def _run_auto_sync_for_profile(profile_id: str):
    lock_fd = _acquire_profile_fetch_lock(profile_id, "auto-sync")
    if lock_fd is None:
        _auto_sync_log(f"Skip {profile_id}: another sync is already running", "WARN")
        return

    started_at = _now_iso()
    _save_auto_sync_state(
        profile_id,
        last_attempt_at=started_at,
        last_status="running",
        last_error=None,
        last_trigger="auto",
    )
    try:
        _auto_sync_log(f"Starting scheduled sync for {profile_id}")
        ok, error_message = _refresh_profile_tokens(profile_id, f"AUTO-{profile_id}")
        if not ok:
            _save_auto_sync_state(
                profile_id,
                last_status="failed",
                last_finished_at=_now_iso(),
                last_error=error_message,
            )
            _auto_sync_log(f"{profile_id} token refresh failed: {error_message}", "ERROR")
            return

        proc = subprocess.Popen(
            [sys.executable, "fetch/fetch_all.py", "--profile", profile_id],
            cwd=os.getcwd(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=0,
            universal_newlines=True,
            encoding="utf-8",
            errors="replace",
            env=_prepare_fetch_env(profile_id),
        )
        output_lines = deque(maxlen=200)
        assert proc.stdout is not None
        for raw_line in proc.stdout:
            line = raw_line.rstrip("\n")
            output_lines.append(line)
            print(f"[AUTO-{profile_id}] {line}")

        return_code = proc.wait()
        finished_at = _now_iso()
        if return_code == 0:
            _save_auto_sync_state(
                profile_id,
                last_status="completed",
                last_finished_at=finished_at,
                last_success_at=finished_at,
                last_error=None,
            )
            _auto_sync_log(f"Completed scheduled sync for {profile_id}")
            return

        error_preview = "\n".join(list(output_lines)[-20:]).strip() or f"fetch_all.py exited with code {return_code}"
        _save_auto_sync_state(
            profile_id,
            last_status="failed",
            last_finished_at=finished_at,
            last_error=error_preview,
        )
        _auto_sync_log(f"Scheduled sync failed for {profile_id} with code {return_code}", "ERROR")
    except Exception as exc:
        _save_auto_sync_state(
            profile_id,
            last_status="error",
            last_finished_at=_now_iso(),
            last_error=str(exc),
        )
        _auto_sync_log(f"Scheduled sync crashed for {profile_id}: {exc}", "ERROR")
    finally:
        _release_profile_fetch_lock(lock_fd)


def run_auto_sync_cycle():
    if not AUTO_SYNC_ENABLED:
        return

    profiles = _discover_syncable_profiles()
    if not profiles:
        _auto_sync_log("The personal account is not authorized; skipping automatic sync")
        return

    now_dt = datetime.now()
    for profile_id in profiles:
        if auto_sync_stop_event.is_set():
            return
        if not _profile_due_for_auto_sync(profile_id, now_dt):
            continue
        _run_auto_sync_for_profile(profile_id)


def _auto_sync_loop():
    if AUTO_SYNC_STARTUP_DELAY_SECONDS > 0:
        if auto_sync_stop_event.wait(AUTO_SYNC_STARTUP_DELAY_SECONDS):
            return

    while not auto_sync_stop_event.is_set():
        run_auto_sync_cycle()
        if auto_sync_stop_event.wait(AUTO_SYNC_SCAN_INTERVAL_SECONDS):
            return


def start_auto_sync_scheduler():
    global auto_sync_thread
    if not AUTO_SYNC_ENABLED:
        _auto_sync_log("Automatic sync disabled by FITBAUS_AUTO_SYNC_ENABLED", "WARN")
        return
    if auto_sync_thread and auto_sync_thread.is_alive():
        _auto_sync_log("Automatic sync scheduler already running")
        return

    auto_sync_stop_event.clear()
    auto_sync_thread = threading.Thread(
        target=_auto_sync_loop,
        name="fitbaus-auto-sync",
        daemon=True,
    )
    auto_sync_thread.start()
    _auto_sync_log(
        f"Automatic sync scheduler started: every {AUTO_SYNC_INTERVAL_SECONDS // 3600}h, scan every {AUTO_SYNC_SCAN_INTERVAL_SECONDS}s"
    )


def stop_auto_sync_scheduler():
    auto_sync_stop_event.set()
    if auto_sync_thread and auto_sync_thread.is_alive():
        auto_sync_thread.join(timeout=5)
    _auto_sync_log("Automatic sync scheduler stopped")


def run_fetch_script(profile_id, job_id):
    """Run fetch_all.py script in background thread with live status updates"""
    _require_owner(profile_id)
    with jobs.lock:
        if jobs.fetch_jobs.get(job_id, {}).get("profile") != profile_id:
            return
    lock_fd = None
    try:
        print(f"[DEBUG] Thread started for job {job_id}")
        print(f"[DEBUG] Current fetch_jobs keys at thread start: {list(jobs.fetch_jobs.keys())}")

        # Check if job exists at thread start
        if job_id not in jobs.fetch_jobs:
            print(f"[DEBUG] ERROR: Job {job_id} not found at thread start!")
            return

        lock_fd = _acquire_profile_fetch_lock(profile_id, f"manual-job-{job_id}")
        if lock_fd is None:
            jobs.fetch_jobs[job_id]['status'] = 'failed'
            jobs.fetch_jobs[job_id]['end_time'] = _now_iso()
            jobs.fetch_jobs[job_id]['error'] = '当前账户已有同步任务在运行。'
            return

        jobs.log_fetch(job_id, f"Starting fetch operation for profile: {profile_id}")
        jobs.log_fetch(job_id, f"Job created at: {datetime.now().isoformat()}")

        # Update job status
        print(f"[DEBUG] Updating job {job_id} status to running")
        if job_id not in jobs.fetch_jobs:
            print(f"[DEBUG] ERROR: Job {job_id} not found in fetch_jobs during status update!")
            print(f"[DEBUG] Available jobs: {list(jobs.fetch_jobs.keys())}")
            return
        jobs.fetch_jobs[job_id]['status'] = 'running'
        jobs.fetch_jobs[job_id]['start_time'] = datetime.now().isoformat()
        print(f"[DEBUG] Job {job_id} status updated, fetch_jobs keys: {list(jobs.fetch_jobs.keys())}")
        # Initialize progress-related fields
        jobs.fetch_jobs[job_id]['current_csv'] = None
        jobs.fetch_jobs[job_id]['start_date'] = None
        jobs.fetch_jobs[job_id]['last_date'] = None
        jobs.fetch_jobs[job_id]['progress'] = 0.0
        jobs.fetch_jobs[job_id]['current_script'] = None
        jobs.fetch_jobs[job_id]['message'] = 'Preparing fetch'
        # Throttling state (API rate limit/backoff)
        jobs.fetch_jobs[job_id]['throttle_active'] = False
        jobs.fetch_jobs[job_id]['throttle_reason'] = None
        jobs.fetch_jobs[job_id]['throttle_mmss'] = None
        jobs.fetch_jobs[job_id]['throttle_until'] = None

        jobs.log_fetch(job_id, "Job state initialized - status: running")

        # Check if profile needs re-authorization first
        try:
            ok, error_message = _refresh_profile_tokens(profile_id, f"FETCH-{job_id}")
            if not ok:
                jobs.fetch_jobs[job_id]['status'] = 'failed'
                jobs.fetch_jobs[job_id]['end_time'] = datetime.now().isoformat()
                jobs.fetch_jobs[job_id]['error'] = error_message
                return
        except Exception as e:
            print(f"Error checking/refreshing tokens for profile {profile_id}: {e}")
            jobs.fetch_jobs[job_id]['status'] = 'failed'
            jobs.fetch_jobs[job_id]['end_time'] = datetime.now().isoformat()
            jobs.fetch_jobs[job_id]['error'] = f'Error checking tokens: {e}'
            return

        # Prepare command
        cmd = [sys.executable, 'fetch/fetch_all.py']
        if profile_id:
            cmd.extend(['--profile', profile_id])

        print(f"Running command: {' '.join(cmd)}")
        print(f"Working directory: {os.getcwd()}")
        print(f"Profile ID: {profile_id}")

        # Set environment variables for proper Unicode handling
        env = _prepare_fetch_env(profile_id)

        print("=" * 60)
        print("FETCH SCRIPT OUTPUT:")
        print("=" * 60)

        # Check if job still exists before starting subprocess
        if job_id not in jobs.fetch_jobs:
            print(f"[DEBUG] ERROR: Job {job_id} not found before subprocess start!")
            print(f"[DEBUG] Current fetch_jobs keys: {list(jobs.fetch_jobs.keys())}")
            return

        # Stream the script output to update progress
        proc = subprocess.Popen(
            cmd,
            cwd=os.getcwd(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=0,  # Unbuffered for immediate output
            universal_newlines=True,
            encoding='utf-8',
            errors='replace',
            env=env,
        )
        # Record process handle for potential cancellation
        jobs.fetch_procs[job_id] = proc

        # Track and parse progress from child output
        output_lines = deque(maxlen=200)
        # Map script names to CSV file names
        script_to_csv = {
            'fetch_steps.py': 'fitbit_activity.csv',
            'fetch_rhr_data.py': 'fitbit_rhr.csv',
            'fetch_hrv_data.py': 'fitbit_hrv.csv',
            'fetch_sleep_data.py': 'fitbit_sleep.csv',
            'fetch_sleep_data_alternate_version.py': 'fitbit_sleep.csv',
            'fetch_profile_snapshot.py': 'fitbit_profile_snapshot.json',
        }

        def update_progress_for(last_date_str: str | None):
            try:
                sd_str = jobs.fetch_jobs[job_id].get('start_date')
                if not sd_str:
                    return
                start_d = _parse_date(sd_str)
                if not start_d:
                    return
                today_d = date.today()
                # If last_date not provided yet, do a tiny non-zero to show activity
                if last_date_str:
                    last_d = _parse_date(last_date_str)
                else:
                    last_d = None
                if not last_d:
                    last_d = start_d
                total_days = max((today_d - start_d).days, 1)
                done_days = max((min(last_d, today_d) - start_d).days, 0)
                progress = max(0.0, min(1.0, done_days / total_days))
                jobs.fetch_jobs[job_id]['progress'] = progress
            except Exception:
                pass

        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.rstrip("\n")
            output_lines.append(line)
            # Debug: print what we're capturing
            print(f"[FETCH-{job_id}] CAPTURED: {line}")

            # Check if job still exists during output processing
            if job_id not in jobs.fetch_jobs:
                print(f"[DEBUG] ERROR: Job {job_id} disappeared during output processing!")
                print(f"[DEBUG] Current fetch_jobs keys: {list(jobs.fetch_jobs.keys())}")
                break
            # Lightweight parsing of progress-relevant lines
            low = line.lower()
            # Detect which script is running
            # Pattern: "[i/N] Starting fetch_xxx.py..." from fetch_all.py
            if 'starting' in low and 'fetch_' in low and low.endswith('...'):
                try:
                    name = line.split('Starting', 1)[1].strip().strip('.').strip('.').strip()
                    # name may include ellipsis; reduce to the script filename
                    parts = name.split()
                    script_name = parts[0] if parts else ''
                    if script_name in script_to_csv:
                        jobs.fetch_jobs[job_id]['current_script'] = script_name
                        jobs.fetch_jobs[job_id]['current_csv'] = script_to_csv[script_name]
                        jobs.fetch_jobs[job_id]['message'] = f"Running {script_name}"
                except Exception:
                    pass

            # Starting range lines per script
            # Steps: Starting activity data fetch from YYYY-MM-DD
            if 'starting activity data fetch from ' in low:
                try:
                    idx = low.index('starting activity data fetch from ')
                    date_str = line[idx:].split('from',1)[1].strip().split()[0]
                    jobs.fetch_jobs[job_id]['start_date'] = date_str
                    jobs.fetch_jobs[job_id]['message'] = f"Activity from {date_str}"
                    update_progress_for(None)
                except Exception:
                    pass
            # RHR: Starting resting HR fetch from YYYY-MM-DD
            if 'starting resting hr fetch from ' in low:
                try:
                    idx = low.index('starting resting hr fetch from ')
                    date_str = line[idx:].split('from',1)[1].strip().split()[0]
                    jobs.fetch_jobs[job_id]['start_date'] = date_str
                    jobs.fetch_jobs[job_id]['message'] = f"RHR from {date_str}"
                    update_progress_for(None)
                except Exception:
                    pass
            # HRV: Starting HRV fetch from YYYY-MM-DD
            if 'starting hrv fetch from ' in low:
                try:
                    idx = low.index('starting hrv fetch from ')
                    date_str = line[idx:].split('from',1)[1].strip().split()[0]
                    jobs.fetch_jobs[job_id]['start_date'] = date_str
                    jobs.fetch_jobs[job_id]['message'] = f"HRV from {date_str}"
                    update_progress_for(None)
                except Exception:
                    pass
            # Sleep: Starting sleep data fetch from YYYY-MM-DD to YYYY-MM-DD
            if 'starting sleep data fetch from ' in low:
                try:
                    idx = low.index('starting sleep data fetch from ')
                    rest = line[idx:].split('from',1)[1].strip()
                    date_str = rest.split()[0]
                    jobs.fetch_jobs[job_id]['start_date'] = date_str
                    jobs.fetch_jobs[job_id]['message'] = f"Sleep from {date_str}"
                    update_progress_for(None)
                except Exception:
                    pass

            # Chunk lines: "Fetching yyyy-mm-dd to yyyy-mm-dd..."
            # Do not update last_date here; this line often prints the target end (e.g., today)
            # before any data is actually saved, which can briefly show 100%.
            # We rely on "Saved ... up to YYYY-MM-DD" lines to advance progress accurately.
            if 'fetching ' in low and ' to ' in low:
                try:
                    # Optionally update the message to reflect current activity without affecting progress
                    parts = line.strip().split()
                    dates = [p for p in parts if len(p) == 10 and p[4] == '-' and p[7] == '-']
                    if len(dates) >= 2:
                        start_candidate = dates[0]
                        end_candidate = dates[1]
                        if _parse_date(start_candidate) and _parse_date(end_candidate):
                            jobs.fetch_jobs[job_id]['message'] = f"Fetching {start_candidate} → {end_candidate}"
                except Exception:
                    pass

            # Saved lines: capture CSV and last date: "Saved ... to <csv> up to YYYY-MM-DD"
            if 'saved ' in low and ' to ' in low:
                try:
                    # Try to infer CSV filename
                    parts = line.strip().split()
                    csv_tokens = [p for p in parts if p.endswith('.csv')]
                    if csv_tokens:
                        jobs.fetch_jobs[job_id]['current_csv'] = os.path.basename(csv_tokens[-1])
                except Exception:
                    pass
            if ' up to ' in low:
                try:
                    after = line.lower().split(' up to ', 1)[1]
                    end_str = after.strip().split()[0]
                    # Validate date
                    if _parse_date(end_str):
                        jobs.fetch_jobs[job_id]['last_date'] = end_str
                        update_progress_for(end_str)
                except Exception:
                    pass

            # Detect throttling/backoff and countdowns
            # Header-provided reset seconds
            if 'rate-limit headers indicate reset in ' in low and 's' in low:
                try:
                    # e.g., "Rate-limit headers indicate reset in 27s."
                    import re
                    m = re.search(r"reset in\s+(\d+)s", low)
                    if m:
                        secs = int(m.group(1))
                        from datetime import timedelta
                        until = (datetime.now() + timedelta(seconds=secs)).strftime('%Y-%m-%d %H:%M:%S')
                        jobs.log_fetch(job_id, f"THROTTLE: Rate-limit headers indicate reset in {secs}s (until {until})", "THROTTLE")
                        jobs.fetch_jobs[job_id]['throttle_active'] = True
                        jobs.fetch_jobs[job_id]['throttle_reason'] = 'Header reset'
                        jobs.fetch_jobs[job_id]['throttle_until'] = until
                        jobs.fetch_jobs[job_id]['throttle_mmss'] = None
                except Exception as e:
                    print(f"[FETCH-{job_id}] ERROR parsing rate-limit reset: {e}")
                    pass
            # Header reset countdown: "Header reset for Xs..."
            if 'header reset for ' in low and 's...' in low:
                try:
                    # e.g., "Header reset for 1200s..."
                    import re
                    m = re.search(r"header reset for\s+(\d+)s", low)
                    if m:
                        secs = int(m.group(1))
                        from datetime import timedelta
                        until = (datetime.now() + timedelta(seconds=secs)).strftime('%Y-%m-%d %H:%M:%S')
                        print(f"[FETCH-{job_id}] THROTTLE: Header reset for {secs}s (until {until})")
                        jobs.fetch_jobs[job_id]['throttle_active'] = True
                        jobs.fetch_jobs[job_id]['throttle_reason'] = 'Header reset'
                        jobs.fetch_jobs[job_id]['throttle_until'] = until
                        jobs.fetch_jobs[job_id]['throttle_mmss'] = None
                except Exception as e:
                    print(f"[FETCH-{job_id}] ERROR parsing header reset: {e}")
                    pass
            # Top-of-hour wait message
            if 'waiting until ' in low and 'top of hour' in low:
                try:
                    # e.g., "Waiting until 14:00:05 (top of hour + 5s)..."
                    after = low.split('waiting until ', 1)[1]
                    until = after.split()[0]
                    # Extract reason prefix (text before ". Waiting") if present
                    reason = line.split('. Waiting', 1)[0]
                    print(f"[FETCH-{job_id}] THROTTLE: {reason} - waiting until {until} (top of hour)")
                    jobs.fetch_jobs[job_id]['throttle_active'] = True
                    jobs.fetch_jobs[job_id]['throttle_reason'] = reason
                    jobs.fetch_jobs[job_id]['throttle_until'] = until
                    jobs.fetch_jobs[job_id]['throttle_mmss'] = None
                except Exception as e:
                    print(f"[FETCH-{job_id}] ERROR parsing top-of-hour wait: {e}")
                    pass
            # Generic countdown: "Retrying in MM:SS" - only update throttle_mmss occasionally
            if 'retrying in ' in low:
                try:
                    # Only update throttle_mmss every 10 seconds to reduce server load
                    import time
                    current_time = time.time()
                    last_update = jobs.fetch_jobs[job_id].get('_last_countdown_update', 0)

                    if current_time - last_update >= 10:  # Update every 10 seconds
                        # Extract last token like 12:34
                        parts = line.strip().split()
                        mmss = None
                        for p in parts[::-1]:
                            if len(p) == 5 and p[2] == ':' and p[:2].isdigit() and p[3:].isdigit():
                                mmss = p
                                break
                        if mmss:
                            current_reason = jobs.fetch_jobs[job_id].get('throttle_reason', 'Backoff')
                            print(f"[FETCH-{job_id}] THROTTLE: {current_reason} - retrying in {mmss}")
                            jobs.fetch_jobs[job_id]['throttle_active'] = True
                            # Keep existing reason if set; otherwise generic
                            if not jobs.fetch_jobs[job_id].get('throttle_reason'):
                                jobs.fetch_jobs[job_id]['throttle_reason'] = 'Backoff'
                            jobs.fetch_jobs[job_id]['throttle_mmss'] = mmss
                            jobs.fetch_jobs[job_id]['_last_countdown_update'] = current_time
                except Exception as e:
                    print(f"[FETCH-{job_id}] ERROR parsing retry countdown: {e}")
                    pass
            # Countdown completion
            if low.strip() == 'resuming...':
                print(f"[FETCH-{job_id}] THROTTLE: Resuming after throttling period")
                jobs.fetch_jobs[job_id]['throttle_active'] = False
                jobs.fetch_jobs[job_id]['throttle_reason'] = None
                jobs.fetch_jobs[job_id]['throttle_mmss'] = None
                jobs.fetch_jobs[job_id]['throttle_until'] = None

        return_code = proc.wait()
        print(f"[FETCH-{job_id}] Process completed with return code: {return_code}")
        print(f"[FETCH-{job_id}] Job status before finalization: {jobs.fetch_jobs.get(job_id, {}).get('status', 'NOT_FOUND')}")

        # Finalize job
        out_text = "\n".join(output_lines)
        print(f"[FETCH-{job_id}] STORED OUTPUT LENGTH: {len(out_text)} characters")
        print(f"[FETCH-{job_id}] STORED OUTPUT PREVIEW: {out_text[:200]}...")
        print(f"[FETCH-{job_id}] RETURN CODE: {return_code}")
        if job_id in jobs.fetch_jobs:
            jobs.fetch_jobs[job_id]['status'] = ('cancelled' if jobs.fetch_jobs[job_id].get('status') == 'cancelled' else 'completed' if return_code == 0 else 'failed')
            jobs.fetch_jobs[job_id]['end_time'] = datetime.now().isoformat()
            jobs.fetch_jobs[job_id]['return_code'] = return_code
            jobs.fetch_jobs[job_id]['output'] = out_text
            jobs.fetch_jobs[job_id]['error'] = None

            print(f"[FETCH-{job_id}] Job finalized with status: {jobs.fetch_jobs[job_id]['status']}")
        else:
            print(f"[FETCH-{job_id}] ERROR: Job {job_id} not found in fetch_jobs during finalization!")

        if return_code == 0:
            print(f"[FETCH-{job_id}] SUCCESS: Fetch completed successfully for profile {profile_id}")
        else:
            print(f"[FETCH-{job_id}] ERROR: Fetch failed for profile {profile_id} with exit code {return_code}")

    except subprocess.TimeoutExpired:
        print(f"[FETCH-{job_id}] TIMEOUT: Script execution timed out after 5 minutes")
        if job_id in jobs.fetch_jobs:
            jobs.fetch_jobs[job_id]['status'] = 'timeout'
            jobs.fetch_jobs[job_id]['end_time'] = datetime.now().isoformat()
            jobs.fetch_jobs[job_id]['error'] = 'Script execution timed out after 5 minutes'
        else:
            print(f"[FETCH-{job_id}] ERROR: Job {job_id} not found in fetch_jobs during timeout handling")
    except Exception as e:
        print(f"[FETCH-{job_id}] EXCEPTION: {str(e)}")
        print(f"[FETCH-{job_id}] Exception type: {type(e).__name__}")
        import traceback
        print(f"[FETCH-{job_id}] Traceback: {traceback.format_exc()}")
        if job_id in jobs.fetch_jobs:
            jobs.fetch_jobs[job_id]['status'] = 'error'
            jobs.fetch_jobs[job_id]['end_time'] = datetime.now().isoformat()
            jobs.fetch_jobs[job_id]['error'] = str(e)
        else:
            print(f"[FETCH-{job_id}] ERROR: Job {job_id} not found in fetch_jobs during exception handling")
    finally:
        # Only clear proc handle if job is actually completed/failed
        try:
            if jobs.fetch_jobs.get(job_id, {}).get('status') in ['completed', 'failed', 'timeout', 'error', 'cancelled']:
                jobs.fetch_procs.pop(job_id, None)

                # Terminal jobs are pruned lazily by the registry.

        except Exception:
            pass
        _release_profile_fetch_lock(lock_fd)
