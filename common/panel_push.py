"""Push the health snapshot to the lucius7.dev panel.

The panel (https://github.com/xw7qwq/panel) never connects to this server. The
scheduler sends it the metric cards, recent daily and weekly series and the
latest sleep records over HTTPS with a dedicated bearer token. The panel shows
only a 7-day average publicly and keeps the rest behind its login.
"""
from __future__ import annotations

import json
import math
import os
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from urllib.parse import urlsplit

from common.dashboard_cache import DATASET_FILES, SNAPSHOT_FILENAME, load_dashboard_cache
from common.profile_paths import cache_path_for, csv_path_for, ensure_dirs_for_cache
from common.public_api import build_series_payload

SCHEMA_VERSION = 1
DAILY_POINTS = 400
WEEKLY_POINTS = 104
DEFAULT_URL = "https://lucius7.dev/api/v1/ingest/health"
STATE_FILENAME = "panel_push.json"
USER_AGENT = "fitflare-panel-push/1 (+https://github.com/xw7qwq/health)"
# The panel's public summary covers the 7 days before today in UTC+8, so it
# needs at least one push after each midnight there even without new data.
PANEL_TZ = timezone(timedelta(hours=8))
RETRY_BASE_SECONDS = 300
RETRY_MAX_SECONDS = 6 * 3600


class PanelPushError(RuntimeError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    # A redirect means a wrong URL (the apex used to redirect to the blog);
    # following it would turn the POST into a GET and hide the mistake.
    def redirect_request(self, *args, **kwargs):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _log(message: str, level: str = "INFO") -> None:
    print(f"[{datetime.now():%H:%M:%S}] [PANEL] [{level}] {message}")


def panel_settings() -> dict[str, str] | None:
    """The ingest URL and token, or None when pushing is not configured."""
    token = os.getenv("FITFLARE_PANEL_INGEST_TOKEN", "").strip()
    if not token:
        return None
    url = os.getenv("FITFLARE_PANEL_INGEST_URL", "").strip() or DEFAULT_URL
    parts = urlsplit(url)
    local = parts.scheme == "http" and parts.hostname in {"127.0.0.1", "localhost"}
    if not parts.hostname or (parts.scheme != "https" and not local):
        raise PanelPushError("FITFLARE_PANEL_INGEST_URL must be an https URL")
    return {"url": url, "token": token}


def _finite(value: Any) -> Any:
    """JSON has no NaN or Infinity; send them as null."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: _finite(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_finite(item) for item in value]
    return value


def _utc_iso(value: Any) -> str | None:
    """Dashboard times are naive container-local times; send them in UTC."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def build_snapshot(profile_id: str) -> dict[str, Any]:
    """Snapshot schema 1, as accepted by the panel's POST /api/v1/ingest/health."""
    dashboard = load_dashboard_cache(profile_id)
    daily, _ = build_series_payload(profile_id, dashboard, "daily", limit=DAILY_POINTS)
    weekly, _ = build_series_payload(profile_id, dashboard, "weekly", limit=WEEKLY_POINTS)
    tables = dashboard.get("tables") or {}
    return _finite(
        {
            "schema_version": SCHEMA_VERSION,
            "generated_at": _utc_iso(dashboard.get("generated_at")),
            "source": "health",
            "metrics": [card for card in dashboard.get("stats") or [] if isinstance(card, dict)],
            "daily": {"metrics": daily["metrics"], "points": daily["points"]},
            "weekly": {"metrics": weekly["metrics"], "points": weekly["points"]},
            "sleep": [row for row in tables.get("sleep") or [] if isinstance(row, dict)],
        }
    )


def encode(snapshot: dict[str, Any]) -> bytes:
    return json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _error_detail(error: urllib.error.HTTPError) -> str:
    try:
        payload = json.loads(error.read().decode("utf-8"))
        detail = payload["error"]
        return f"{detail.get('code')}: {detail.get('message')}"[:200]
    except Exception:
        return str(error.reason)[:200]


def send_snapshot(
    snapshot: dict[str, Any],
    url: str,
    token: str,
    *,
    timeout: float = 30,
    attempts: int = 3,
    sleep: Callable[[float], None] | None = None,
) -> dict[str, Any]:
    """POST the snapshot; retry rate limits, server errors and network failures."""
    request = urllib.request.Request(
        url,
        data=encode(snapshot),
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    for attempt in range(1, attempts + 1):
        try:
            with _OPENER.open(request, timeout=timeout) as response:
                text = response.read().decode("utf-8", errors="replace")
            try:
                payload = json.loads(text or "{}")
            except ValueError:
                raise PanelPushError("panel answered with a body that is not JSON") from None
            data = payload.get("data") if isinstance(payload, dict) else None
            return data if isinstance(data, dict) else {}
        except urllib.error.HTTPError as error:
            retryable = error.code == 429 or error.code >= 500
            if not retryable or attempt == attempts:
                raise PanelPushError(f"panel answered {error.code} ({_error_detail(error)})") from None
        except (urllib.error.URLError, OSError) as error:
            if attempt == attempts:
                raise PanelPushError(f"panel unreachable ({getattr(error, 'reason', error)})") from None
        (sleep or time.sleep)(2 ** attempt)
    raise PanelPushError("panel push was not attempted")


def _state_path(profile_id: str) -> str:
    return cache_path_for(profile_id, STATE_FILENAME)


def load_state(profile_id: str) -> dict[str, Any]:
    try:
        with open(_state_path(profile_id), "r", encoding="utf-8") as handle:
            state = json.load(handle)
        return state if isinstance(state, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(profile_id: str, state: dict[str, Any]) -> None:
    path = _state_path(profile_id)
    ensure_dirs_for_cache(path)
    descriptor, temp_path = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".panel_push.", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, indent=2)
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def source_mtime(profile_id: str) -> float:
    """Newest modification time of the synced files the snapshot is built from."""
    paths = [csv_path_for(profile_id, filename) for filename in DATASET_FILES.values()]
    paths.append(cache_path_for(profile_id, SNAPSHOT_FILENAME))
    return max((os.path.getmtime(path) for path in paths if os.path.exists(path)), default=0.0)


def push_due(state: dict[str, Any], mtime: float, now: datetime) -> bool:
    """New synced data, or no successful push yet today in UTC+8; failures back off."""
    failures = int(state.get("failures") or 0)
    if failures:
        wait = min(RETRY_BASE_SECONDS * 2 ** (failures - 1), RETRY_MAX_SECONDS)
        if now.timestamp() - float(state.get("last_failure_ts") or 0) < wait:
            return False
    if state.get("last_success_day") != now.astimezone(PANEL_TZ).date().isoformat():
        return True
    return mtime > float(state.get("source_mtime") or 0)


def push(profile_id: str, settings: dict[str, str] | None = None, now: datetime | None = None) -> dict[str, Any]:
    """Build and send the snapshot now, recording the outcome for the scheduler."""
    settings = settings or panel_settings()
    if not settings:
        raise PanelPushError("FITFLARE_PANEL_INGEST_TOKEN is not set")
    now = now or datetime.now(timezone.utc)
    state = load_state(profile_id)
    mtime = source_mtime(profile_id)
    try:
        snapshot = build_snapshot(profile_id)
        result = send_snapshot(snapshot, settings["url"], settings["token"])
    except Exception as error:
        state.update(
            failures=int(state.get("failures") or 0) + 1,
            last_failure_at=now.isoformat(timespec="seconds"),
            last_failure_ts=now.timestamp(),
            last_error=str(error)[:300],
        )
        _save_state(profile_id, state)
        raise
    _save_state(
        profile_id,
        {
            "last_success_at": now.isoformat(timespec="seconds"),
            "last_success_day": now.astimezone(PANEL_TZ).date().isoformat(),
            "source_mtime": mtime,
            "failures": 0,
            "daily_points": len(snapshot["daily"]["points"]),
            "stored": result.get("stored"),
            "bytes": result.get("bytes"),
        },
    )
    return result


def push_if_due(profile_id: str, log: Callable[..., None] = _log) -> bool:
    """Scheduler hook: push after new data arrives and at least once per UTC+8 day.

    Never raises, so a misconfiguration cannot stop the sync scheduler.
    """
    try:
        settings = panel_settings()
        if not settings:
            return False
        now = datetime.now(timezone.utc)
        if not push_due(load_state(profile_id), source_mtime(profile_id), now):
            return False
        result = push(profile_id, settings, now)
    except Exception as error:
        log(f"Push failed: {error}", "ERROR")
        return False
    log(f"Pushed the snapshot ({result.get('bytes')} bytes, {'stored' if result.get('stored') else 'unchanged'})")
    return True
