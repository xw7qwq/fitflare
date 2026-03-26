#!/usr/bin/env python3
"""
FitBaus Flask Server
Replaces the built-in HTTP server with Flask to support API endpoints
while maintaining all existing static file serving functionality.
"""

import os
import subprocess
import threading
import json
import time
import fcntl
import hmac
import secrets
from datetime import datetime, date, timedelta
from functools import wraps
from flask import Flask, send_from_directory, send_file, request, jsonify, session, make_response
from flask_cors import CORS
from werkzeug.security import check_password_hash

from common.dashboard_cache import build_dashboard_cache, build_profile_cards, load_dashboard_cache
from common.fitbit_scopes import FITBIT_DASHBOARD_SCOPE_TEXT
from common.profile_paths import list_profiles as list_profile_ids
from common.public_api import (
    PUBLIC_API_BASE_PATH,
    build_chart_svg,
    build_dataset_payload,
    build_envelope,
    build_metric_payload,
    build_openapi_spec,
    build_section_payload,
    build_series_payload,
    build_table_payload,
    dataset_keys,
    parse_int_arg,
    public_dashboard_payload,
    public_snapshot_payload,
    section_keys,
    svg_chart_presets,
    table_keys,
)

app = Flask(__name__)
CORS(app)  # Enable CORS for API endpoints


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _env_int(name: str, default: int, minimum: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return max(minimum, int(raw))
    except Exception:
        return default


def _env_text(name: str, default: str = "") -> str:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip()


AUTO_SYNC_ENABLED = _env_flag("FITBAUS_AUTO_SYNC_ENABLED", True)
AUTO_SYNC_INTERVAL_SECONDS = _env_int("FITBAUS_AUTO_SYNC_INTERVAL_SECONDS", 6 * 60 * 60, 60)
AUTO_SYNC_SCAN_INTERVAL_SECONDS = _env_int("FITBAUS_AUTO_SYNC_SCAN_INTERVAL_SECONDS", 5 * 60, 30)
AUTO_SYNC_STARTUP_DELAY_SECONDS = _env_int("FITBAUS_AUTO_SYNC_STARTUP_DELAY_SECONDS", 45, 0)
ADMIN_PASSWORD = _env_text("FITBAUS_ADMIN_PASSWORD")
ADMIN_PASSWORD_HASH = _env_text("FITBAUS_ADMIN_PASSWORD_HASH")
ADMIN_AUTH_CONFIGURED = bool(ADMIN_PASSWORD or ADMIN_PASSWORD_HASH)
SESSION_SECRET = _env_text("FITBAUS_SESSION_SECRET")
SESSION_COOKIE_SECURE = _env_flag("FITBAUS_SESSION_COOKIE_SECURE", True)
auto_sync_thread = None
auto_sync_stop_event = threading.Event()

if not SESSION_SECRET:
    SESSION_SECRET = ADMIN_PASSWORD_HASH or ADMIN_PASSWORD or secrets.token_hex(32)

app.config.update(
    SECRET_KEY=SESSION_SECRET,
    SESSION_COOKIE_NAME="fitbaus_admin_session",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=SESSION_COOKIE_SECURE,
    PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
)

NO_STORE_PATHS = {
    '/',
    '/index.html',
    '/app.js',
    '/style.css',
    '/version.js',
    '/mobile.html',
    '/spousal.html',
    '/script.js',
    '/ui-cn.js',
}

PUBLIC_STATIC_FILES = {
    'index.html',
    'app.js',
    'style.css',
    'version.js',
    'mobile.html',
    'spousal.html',
    'script.js',
    'ui-cn.js',
}

PUBLIC_STATIC_PREFIXES = (
    'assets/',
)


@app.after_request
def apply_cache_headers(response):
    """Keep HTML/CSS/JS fresh so deploys do not mix new markup with stale assets."""
    if request.path in NO_STORE_PATHS:
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
    return response

# Global state for tracking fetch operations
class FetchJobsDict(dict):
    def __init__(self):
        super().__init__()
        self._lock = threading.Lock()
    
    def __setitem__(self, key, value):
        with self._lock:
            print(f"[DEBUG] Setting fetch_jobs[{key}] = {value.get('status', 'unknown') if isinstance(value, dict) else value}")
            super().__setitem__(key, value)
            print(f"[DEBUG] fetch_jobs keys after set: {list(self.keys())}")
    
    def __delitem__(self, key):
        with self._lock:
            print(f"[DEBUG] Deleting fetch_jobs[{key}]")
            super().__delitem__(key)
            print(f"[DEBUG] fetch_jobs keys after delete: {list(self.keys())}")
    
    def clear(self):
        with self._lock:
            print(f"[DEBUG] WARNING: fetch_jobs.clear() called! Keys before clear: {list(self.keys())}")
            print(f"[DEBUG] WARNING: Stack trace for clear() call:")
            import traceback
            traceback.print_stack()
            super().clear()
            print(f"[DEBUG] fetch_jobs cleared - now empty")
    
    def pop(self, key, default=None):
        with self._lock:
            if key in self:
                print(f"[DEBUG] Popping fetch_jobs[{key}]")
                result = super().pop(key, default)
                print(f"[DEBUG] fetch_jobs keys after pop: {list(self.keys())}")
                return result
            return default

fetch_jobs = FetchJobsDict()

# Add a check to see if fetch_jobs gets reassigned
_original_fetch_jobs = fetch_jobs
def check_fetch_jobs_reassignment():
    global fetch_jobs
    if fetch_jobs is not _original_fetch_jobs:
        print(f"[DEBUG] WARNING: fetch_jobs has been reassigned! Original: {_original_fetch_jobs}, Current: {fetch_jobs}")
        print(f"[DEBUG] WARNING: Stack trace for reassignment:")
        import traceback
        traceback.print_stack()
        fetch_jobs = _original_fetch_jobs  # Restore original
# Track running fetch subprocesses by job_id for cancellation
fetch_procs = {}
job_counter = 0

# Global state for tracking authorization operations
auth_jobs = {}
auth_job_counter = 0

# Verbose logging configuration
VERBOSE_FETCH_LOGGING = True  # Set to False to disable verbose fetch logging

def _log_fetch(job_id: str, message: str, level: str = "INFO"):
    """Helper function for verbose fetch logging"""
    if VERBOSE_FETCH_LOGGING:
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"[{timestamp}] [FETCH-{job_id}] [{level}] {message}")

def _parse_date(s: str) -> date | None:
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except Exception:
        return None


def _now_iso() -> str:
    return datetime.now().isoformat()


def _normalize_public_path(filename: str | None) -> str | None:
    if not filename:
        return None
    normalized = os.path.normpath(str(filename)).replace("\\", "/").lstrip("./")
    if not normalized or normalized == ".":
        return None
    if normalized.startswith("../") or "/../" in normalized or normalized == "..":
        return None
    return normalized


def _is_public_static_path(filename: str) -> bool:
    normalized = _normalize_public_path(filename)
    if not normalized:
        return False
    if normalized in PUBLIC_STATIC_FILES:
        return True
    return normalized.startswith(PUBLIC_STATIC_PREFIXES)


def _verify_admin_password(password: str) -> bool:
    if not ADMIN_AUTH_CONFIGURED:
        return False
    candidate = (password or "").strip()
    if not candidate:
        return False
    if ADMIN_PASSWORD_HASH:
        try:
            return check_password_hash(ADMIN_PASSWORD_HASH, candidate)
        except Exception:
            return False
    return hmac.compare_digest(candidate, ADMIN_PASSWORD)


def _admin_session_payload() -> dict:
    authenticated = bool(ADMIN_AUTH_CONFIGURED and session.get("is_admin"))
    return {
        "configured": ADMIN_AUTH_CONFIGURED,
        "authenticated": authenticated,
        "csrf_token": session.get("csrf_token") if authenticated else None,
    }


def _set_admin_session():
    session.clear()
    session.permanent = True
    session["is_admin"] = True
    session["csrf_token"] = secrets.token_urlsafe(24)
    session["login_at"] = _now_iso()


def _clear_admin_session():
    session.clear()


def _admin_error(message: str, status_code: int, code: str):
    return jsonify({
        "error": message,
        "code": code,
        "configured": ADMIN_AUTH_CONFIGURED,
        "authenticated": bool(session.get("is_admin")),
        "csrf_token": None,
    }), status_code


def require_admin(csrf: bool = False):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            if not ADMIN_AUTH_CONFIGURED:
                return _admin_error("管理员口令尚未配置，管理功能已停用。", 503, "admin_not_configured")

            if not session.get("is_admin"):
                return _admin_error("需要管理员登录。", 401, "admin_auth_required")

            if csrf and request.method not in ("GET", "HEAD", "OPTIONS"):
                csrf_token = request.headers.get("X-FitBaus-CSRF", "").strip()
                session_token = str(session.get("csrf_token") or "")
                if not csrf_token or not session_token or not hmac.compare_digest(csrf_token, session_token):
                    return _admin_error("管理员会话已失效，请重新登录。", 403, "invalid_admin_csrf")

            return func(*args, **kwargs)

        return wrapper

    return decorator


def _public_api_error(message: str, status_code: int = 400, code: str = "bad_request"):
    response = jsonify({
        "api_version": "v1",
        "error": {
            "code": code,
            "message": message,
        },
    })
    response.status_code = status_code
    response.headers["Cache-Control"] = "no-store"
    return response


def _public_json_response(payload: dict, status_code: int = 200, max_age: int = 300):
    response = jsonify(payload)
    response.status_code = status_code
    response.headers["Cache-Control"] = f"public, max-age={max_age}, stale-while-revalidate={max_age * 2}"
    return response


def _public_text_response(body: str, mimetype: str, status_code: int = 200, max_age: int = 300):
    response = make_response(body, status_code)
    response.mimetype = mimetype
    response.headers["Cache-Control"] = f"public, max-age={max_age}, stale-while-revalidate={max_age * 2}"
    return response


def _public_file_response(path: str, mimetype: str, max_age: int = 300):
    response = make_response(send_file(path, mimetype=mimetype))
    response.headers["Cache-Control"] = f"public, max-age={max_age}, stale-while-revalidate={max_age * 2}"
    return response


def _profile_exists(profile_id: str) -> bool:
    return os.path.isdir(os.path.join("profiles", profile_id))


def _load_public_dashboard(profile_id: str) -> dict | None:
    if not _profile_exists(profile_id):
        return None
    return load_dashboard_cache(profile_id, rebuild_if_missing=True)


def _public_profile_links(base_url: str, profile_id: str) -> dict[str, str]:
    root = f"{base_url}{PUBLIC_API_BASE_PATH}/profiles/{profile_id}"
    return {
        "self": root,
        "dashboard": f"{root}/dashboard",
        "overview": f"{root}/overview",
        "coverage": f"{root}/coverage",
        "metrics": f"{root}/metrics",
        "correlations": f"{root}/correlations",
        "series_daily": f"{root}/series/daily",
        "series_weekly": f"{root}/series/weekly",
        "datasets": f"{root}/datasets",
        "sections": f"{root}/sections",
        "tables": f"{root}/tables",
        "snapshot": f"{root}/snapshot",
        "chart_overview": f"{root}/charts/overview-trend.svg",
        "chart_weekly": f"{root}/charts/weekly-trend.svg",
    }


def _public_api_docs_html(base_url: str) -> str:
    sample_profile = next(iter(list_profile_ids()), "YOUR_PROFILE")
    api_root = f"{base_url}{PUBLIC_API_BASE_PATH}"
    sample_root = f"{base_url}{PUBLIC_API_BASE_PATH}/profiles/{sample_profile}"
    docs_md = f"{base_url}{PUBLIC_API_BASE_PATH}/docs.md"
    openapi_json = f"{base_url}{PUBLIC_API_BASE_PATH}/openapi.json"
    profiles_url = f"{api_root}/profiles"
    sample_dashboard = f"{sample_root}/dashboard"
    sample_series = f"{sample_root}/series/daily?metrics=sleep_score,steps,hrv&limit=30"
    sample_chart = f"{sample_root}/charts/series.svg?granularity=daily&metrics=sleep_score,hrv,rhr&limit=30"
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>FitBaus Public API</title>
  <style>
    :root {{
      --bg: #f7faff;
      --surface: rgba(255, 255, 255, 0.94);
      --surface-soft: rgba(248, 251, 255, 0.9);
      --text: #16253d;
      --muted: #66748c;
      --blue: #1a73e8;
      --green: #188038;
      --amber: #f9ab00;
      --border: rgba(26, 115, 232, 0.11);
      --border-strong: rgba(26, 115, 232, 0.18);
      --shadow: 0 18px 46px rgba(31, 53, 96, 0.12);
      --shadow-soft: 0 12px 28px rgba(31, 53, 96, 0.08);
      --radius-xl: 28px;
      --radius-lg: 22px;
      --radius-md: 16px;
    }}
    * {{ box-sizing: border-box; }}
    html {{ scroll-behavior: smooth; }}
    body {{
      margin: 0;
      font-family: "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Segoe UI", sans-serif;
      color: var(--text);
      background:
        radial-gradient(circle at 12% 12%, rgba(26, 115, 232, 0.09), transparent 24%),
        radial-gradient(circle at 88% 8%, rgba(249, 171, 0, 0.12), transparent 22%),
        linear-gradient(180deg, #f8fbff 0%, #eef4fb 100%);
      -webkit-font-smoothing: antialiased;
      text-rendering: optimizeLegibility;
    }}
    a {{ color: inherit; }}
    .shell {{
      max-width: 1240px;
      margin: 0 auto;
      padding: 28px 20px 64px;
    }}
    .hero,
    .card,
    .section-card,
    .toc,
    .metric-pill {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: var(--radius-xl);
      box-shadow: var(--shadow);
      backdrop-filter: blur(14px);
    }}
    .hero {{
      padding: 30px;
      display: grid;
      grid-template-columns: minmax(0, 1.6fr) minmax(320px, 0.9fr);
      gap: 20px;
      background:
        linear-gradient(180deg, rgba(255, 255, 255, 0.98), rgba(246, 250, 255, 0.93)),
        radial-gradient(circle at top right, rgba(26, 115, 232, 0.08), transparent 30%);
    }}
    .eyebrow {{
      color: var(--blue);
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0.12em;
      text-transform: uppercase;
    }}
    h1, h2, h3, p, pre {{ margin: 0; }}
    h1 {{
      font-size: clamp(34px, 5vw, 56px);
      line-height: 1.02;
      letter-spacing: -0.04em;
    }}
    h2 {{
      font-size: 26px;
      letter-spacing: -0.03em;
    }}
    h3 {{
      font-size: 18px;
      letter-spacing: -0.02em;
    }}
    .subtitle {{
      color: var(--muted);
      max-width: 70ch;
      line-height: 1.7;
      margin-top: 12px;
    }}
    .actions {{
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      margin-top: 20px;
    }}
    .button {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      border-radius: 999px;
      padding: 12px 18px;
      font-weight: 700;
      text-decoration: none;
      color: white;
      background: linear-gradient(135deg, #1a73e8, #4d8ff0);
      box-shadow: 0 10px 24px rgba(26, 115, 232, 0.2);
    }}
    .button.alt {{
      color: var(--text);
      background: white;
      border: 1px solid var(--border);
      box-shadow: none;
    }}
    .hero-pills,
    .metric-grid,
    .feature-grid,
    .link-grid,
    .endpoint-grid,
    .example-grid,
    .toc-grid {{
      display: grid;
      gap: 14px;
    }}
    .hero-pills {{
      grid-template-columns: repeat(3, minmax(0, 1fr));
      margin-top: 18px;
    }}
    .metric-grid {{
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }}
    .feature-grid {{
      grid-template-columns: repeat(3, minmax(0, 1fr));
    }}
    .link-grid,
    .example-grid {{
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }}
    .endpoint-grid {{
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }}
    .toc-grid {{
      grid-template-columns: repeat(4, minmax(0, 1fr));
    }}
    .card,
    .section-card {{
      padding: 24px;
      display: grid;
      gap: 12px;
    }}
    .card {{
      border-radius: var(--radius-lg);
      box-shadow: var(--shadow-soft);
    }}
    .hero-copy {{
      display: grid;
      gap: 6px;
    }}
    .hero-aside {{
      border-radius: calc(var(--radius-xl) - 4px);
      padding: 24px;
      background:
        linear-gradient(160deg, #1a73e8, #0f9d58);
      color: white;
      display: grid;
      gap: 18px;
      align-content: start;
    }}
    .hero-aside .eyebrow,
    .hero-aside .muted {{
      color: rgba(255, 255, 255, 0.78);
    }}
    .hero-aside .value {{
      font-size: clamp(38px, 5vw, 56px);
      line-height: 0.96;
      font-weight: 800;
      letter-spacing: -0.04em;
    }}
    .hero-aside .metric-pill {{
      background: rgba(255, 255, 255, 0.14);
      border: 1px solid rgba(255, 255, 255, 0.18);
      box-shadow: none;
      border-radius: var(--radius-md);
      padding: 16px;
    }}
    .hero-aside .metric-pill strong {{
      display: block;
      margin-top: 6px;
      font-size: 20px;
      line-height: 1.3;
    }}
    .label {{
      font-size: 12px;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }}
    .path-box,
    code,
    pre {{
      font-family: ui-monospace, "SFMono-Regular", "Cascadia Code", "Liberation Mono", monospace;
      background: rgba(26, 115, 232, 0.05);
      border-radius: 16px;
    }}
    code {{ padding: 3px 7px; }}
    .path-box {{
      display: inline-flex;
      align-items: center;
      width: fit-content;
      max-width: 100%;
      padding: 10px 14px;
      margin-top: 18px;
      color: #22314f;
      word-break: break-all;
    }}
    pre {{
      padding: 16px;
      overflow: auto;
      line-height: 1.6;
      font-size: 13px;
      color: #22314f;
    }}
    ul {{
      margin: 0;
      padding-left: 18px;
      color: var(--muted);
      line-height: 1.7;
    }}
    .toc {{
      margin-top: 18px;
      padding: 18px;
      border-radius: var(--radius-lg);
      box-shadow: var(--shadow-soft);
    }}
    .toc a {{
      display: block;
      padding: 14px 16px;
      border-radius: var(--radius-md);
      background: var(--surface-soft);
      border: 1px solid rgba(26, 115, 232, 0.08);
      text-decoration: none;
      transition: transform 180ms ease, border-color 180ms ease, box-shadow 180ms ease;
    }}
    .toc a:hover {{
      transform: translateY(-1px);
      border-color: var(--border-strong);
      box-shadow: var(--shadow-soft);
    }}
    .toc a strong {{
      display: block;
      font-size: 16px;
      margin-top: 6px;
    }}
    .stack {{
      display: grid;
      gap: 18px;
      margin-top: 18px;
    }}
    .section-card {{
      background: linear-gradient(180deg, rgba(255, 255, 255, 0.98), rgba(246, 250, 255, 0.93));
    }}
    .section-head {{
      display: flex;
      justify-content: space-between;
      gap: 18px;
      align-items: flex-start;
      margin-bottom: 18px;
    }}
    .section-head p {{
      max-width: 50ch;
      color: var(--muted);
      line-height: 1.7;
    }}
    .feature-card,
    .endpoint-card {{
      padding: 20px;
      border-radius: var(--radius-lg);
      background: var(--surface-soft);
      border: 1px solid rgba(26, 115, 232, 0.08);
      box-shadow: var(--shadow-soft);
    }}
    .feature-card p,
    .endpoint-card p,
    .hint,
    .muted {{
      color: var(--muted);
      line-height: 1.7;
    }}
    .status-row {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
    }}
    .status-chip {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 10px 14px;
      border-radius: 999px;
      background: rgba(26, 115, 232, 0.08);
      color: var(--blue);
      font-size: 13px;
      font-weight: 700;
    }}
    .status-chip.safe {{
      background: rgba(24, 128, 56, 0.1);
      color: var(--green);
    }}
    .status-chip.light {{
      background: rgba(249, 171, 0, 0.12);
      color: #9b6600;
    }}
    .endpoint-list {{
      list-style: none;
      padding: 0;
      margin: 0;
      display: grid;
      gap: 12px;
    }}
    .endpoint-list li {{
      padding: 14px 16px;
      border-radius: var(--radius-md);
      background: white;
      border: 1px solid rgba(26, 115, 232, 0.08);
    }}
    .endpoint-list code {{
      display: inline-block;
      margin-bottom: 6px;
      padding: 0;
      background: transparent;
      color: #21304d;
      font-size: 13px;
    }}
    .endpoint-list span {{
      display: block;
      color: var(--muted);
      line-height: 1.65;
      font-size: 14px;
    }}
    .param-list {{
      list-style: none;
      padding: 0;
      margin: 0;
      display: grid;
      gap: 10px;
    }}
    .param-list li {{
      padding: 12px 14px;
      border-radius: var(--radius-md);
      background: white;
      border: 1px solid rgba(26, 115, 232, 0.08);
      color: var(--muted);
      line-height: 1.65;
    }}
    .param-list strong {{
      color: var(--text);
    }}
    .footer-note {{
      margin-top: 18px;
      padding: 18px 20px;
      border-radius: var(--radius-lg);
      background: rgba(26, 115, 232, 0.05);
      border: 1px solid rgba(26, 115, 232, 0.09);
      color: var(--muted);
      line-height: 1.7;
    }}
    .footer-note strong {{
      color: var(--text);
    }}
    .section-anchor {{
      scroll-margin-top: 18px;
    }}
    @media (max-width: 1080px) {{
      .hero,
      .feature-grid,
      .toc-grid,
      .link-grid,
      .endpoint-grid,
      .example-grid {{
        grid-template-columns: 1fr;
      }}
      .hero-pills,
      .metric-grid {{
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }}
      .section-head {{
        flex-direction: column;
      }}
    }}
    @media (max-width: 720px) {{
      .shell {{
        padding: 18px 14px 36px;
      }}
      .hero,
      .card,
      .section-card,
      .toc {{
        border-radius: 24px;
      }}
      .hero,
      .section-card {{
        padding: 22px;
      }}
      .hero-pills,
      .metric-grid {{
        grid-template-columns: 1fr;
      }}
      .actions {{
        flex-direction: column;
      }}
      .button {{
        width: 100%;
      }}
    }}
  </style>
</head>
<body>
  <div class="shell">
    <section class="hero">
      <div class="hero-copy">
        <div class="eyebrow">FitBaus Public API</div>
        <h1>公开只读健康数据 API</h1>
        <p class="subtitle">面向其他项目复用 Fitbit 本地缓存数据。接口按版本路径输出，分成完整缓存数据集、轻量趋势序列、公开仪表盘摘要和可直接嵌入的 SVG 图表，不开放创建、授权、删除等管理操作。</p>
        <div class="path-box">{api_root}</div>
        <div class="hero-pills">
          <article class="card">
            <div class="label">版本</div>
            <h3>当前公开版本</h3>
            <p class="muted">路径固定在 <code>{PUBLIC_API_BASE_PATH}</code>，方便其他项目稳定集成。</p>
          </article>
          <article class="card">
            <div class="label">示例档案</div>
            <h3>{sample_profile}</h3>
            <p class="muted">所有示例链接都基于当前可公开访问的首个档案生成。</p>
          </article>
          <article class="card">
            <div class="label">输出类型</div>
            <h3>JSON + SVG</h3>
            <p class="muted">既可以拿结构化数据，也可以直接嵌入趋势图资源。</p>
          </article>
        </div>
        <div class="actions">
          <a class="button" href="{openapi_json}">OpenAPI JSON</a>
          <a class="button alt" href="{docs_md}">Markdown 文档</a>
        </div>
      </div>

      <aside class="hero-aside">
        <div class="eyebrow">Quick Snapshot</div>
        <div class="value">6 类</div>
        <p class="muted">覆盖仪表盘、完整 datasets、趋势序列、sections、tables、snapshot 和 SVG 图表接口。</p>
        <div class="metric-grid">
          <div class="metric-pill">
            <div class="label">读取模式</div>
            <strong>公开只读</strong>
          </div>
          <div class="metric-pill">
            <div class="label">适合场景</div>
            <strong>服务集成 / 图表复用</strong>
          </div>
          <div class="metric-pill">
            <div class="label">默认入口</div>
            <strong>/profiles / dashboard</strong>
          </div>
          <div class="metric-pill">
            <div class="label">图表格式</div>
            <strong>image/svg+xml</strong>
          </div>
        </div>
      </aside>
    </section>

    <nav class="toc">
      <div class="label">导航</div>
      <div class="toc-grid">
        <a href="#quickstart">
          <span class="label">Start</span>
          <strong>快速开始</strong>
        </a>
        <a href="#resources">
          <span class="label">Resources</span>
          <strong>资源分层</strong>
        </a>
        <a href="#endpoints">
          <span class="label">Endpoints</span>
          <strong>端点分组</strong>
        </a>
        <a href="#examples">
          <span class="label">Examples</span>
          <strong>调用示例</strong>
        </a>
      </div>
    </nav>

    <div class="stack">
      <section class="section-card section-anchor" id="quickstart">
        <div class="section-head">
          <div>
            <div class="eyebrow">Quick Start</div>
            <h2>推荐从这里开始</h2>
          </div>
          <p>如果你是第一次接入，先拿公开档案列表，再读某个档案的 dashboard 或 series。这样能最快确认你的项目需要走“完整数据集”还是“轻量趋势图”。</p>
        </div>
        <div class="feature-grid">
          <article class="feature-card">
            <div class="label">Step 1</div>
            <h3>列出公开档案</h3>
            <p>先拿所有可公开读取的 profile，再决定用哪个档案做集成。</p>
            <pre>GET {profiles_url}</pre>
          </article>
          <article class="feature-card">
            <div class="label">Step 2</div>
            <h3>读取完整仪表盘</h3>
            <p>需要直接复用现成健康页面结构时，优先使用 dashboard。</p>
            <pre>GET {sample_dashboard}</pre>
          </article>
          <article class="feature-card">
            <div class="label">Step 3</div>
            <h3>切到轻量趋势接口</h3>
            <p>前端图表或其他服务只需要少量时间序列时，改用 series 或 SVG。</p>
            <pre>GET {sample_series}</pre>
          </article>
        </div>
      </section>

      <section class="section-card section-anchor" id="resources">
        <div class="section-head">
          <div>
            <div class="eyebrow">Resource Layers</div>
            <h2>资源分层</h2>
          </div>
          <p>接口按“完整缓存、轻量趋势、摘要卡片、表格、快照、图表”分层。这样其他项目可以按体积和场景选择，不需要反复解析整个 dashboard。</p>
        </div>
        <div class="feature-grid">
          <article class="feature-card">
            <div class="label">Dashboard</div>
            <h3>完整公开仪表盘</h3>
            <p>适合复用 FitBaus 中文健康页的数据结构，包含 overview、coverage、stats、charts、tables。</p>
          </article>
          <article class="feature-card">
            <div class="label">Datasets / Series</div>
            <h3>缓存与趋势双模式</h3>
            <p><code>/datasets</code> 取结构化缓存，<code>/series</code> 取轻量时间序列，两者职责明确。</p>
          </article>
          <article class="feature-card">
            <div class="label">Snapshot / SVG</div>
            <h3>快照和图形资源</h3>
            <p><code>/snapshot</code> 用来复用 Fitbit 元数据，<code>/charts/*.svg</code> 适合直接嵌入页面或报告。</p>
          </article>
        </div>
        <div class="status-row">
          <span class="status-chip safe">只读公开接口</span>
          <span class="status-chip">JSON 统一版本化路径</span>
          <span class="status-chip light">快照已净化，不含 token 元数据</span>
        </div>
      </section>

      <section class="section-card section-anchor" id="endpoints">
        <div class="section-head">
          <div>
            <div class="eyebrow">Endpoint Groups</div>
            <h2>端点分组</h2>
          </div>
          <p>布局按最常用的调用路径重新整理，不再只靠几块示例卡片展示。每组给出主入口和用途，方便你快速定位到正确层级。</p>
        </div>
        <div class="endpoint-grid">
          <article class="endpoint-card">
            <div class="label">Profiles & Dashboard</div>
            <h3>档案与公开仪表盘</h3>
            <ul class="endpoint-list">
              <li><code>GET {api_root}</code><span>返回 API 索引和公开文档链接。</span></li>
              <li><code>GET {profiles_url}</code><span>返回公开档案列表和快捷链接。</span></li>
              <li><code>GET {sample_root}</code><span>返回单个公开档案的概要信息、覆盖范围和可调用链接。</span></li>
              <li><code>GET {sample_dashboard}</code><span>返回完整公开 dashboard 数据，适合页面复用。</span></li>
            </ul>
          </article>

          <article class="endpoint-card">
            <div class="label">Datasets</div>
            <h3>完整缓存数据集</h3>
            <ul class="endpoint-list">
              <li><code>GET {sample_root}/datasets</code><span>列出所有可用 dataset。</span></li>
              <li><code>GET {sample_root}/datasets/activity?limit=120</code><span>读取活动历史缓存。</span></li>
              <li><code>GET {sample_root}/datasets/sleep?limit=120</code><span>读取睡眠历史缓存。</span></li>
              <li><code>GET {sample_root}/datasets/daily?limit=90</code><span>读取服务端聚合后的日趋势数据。</span></li>
            </ul>
          </article>

          <article class="endpoint-card">
            <div class="label">Series & Sections</div>
            <h3>轻量趋势与摘要结构</h3>
            <ul class="endpoint-list">
              <li><code>GET {sample_root}/series/daily?metrics=sleep_score,steps,hrv&amp;limit=30</code><span>返回轻量级日趋势序列。</span></li>
              <li><code>GET {sample_root}/sections</code><span>返回 body、vitals、lifestyle、account 摘要卡片。</span></li>
              <li><code>GET {sample_root}/tables</code><span>返回 sleep、activity、devices 等表格结构。</span></li>
              <li><code>GET {sample_root}/metrics</code><span>返回页面指标卡和趋势信息。</span></li>
            </ul>
          </article>

          <article class="endpoint-card">
            <div class="label">Snapshot & Charts</div>
            <h3>快照缓存与 SVG 图表</h3>
            <ul class="endpoint-list">
              <li><code>GET {sample_root}/snapshot</code><span>返回净化后的 Fitbit 快照缓存。</span></li>
              <li><code>GET {sample_root}/snapshot/endpoints/profile</code><span>读取某个 Fitbit 快照端点。</span></li>
              <li><code>GET {sample_root}/charts/overview-trend.svg</code><span>直接返回预置趋势图 SVG。</span></li>
              <li><code>GET {sample_chart}</code><span>按粒度和指标自定义轻量 SVG 走势。</span></li>
            </ul>
          </article>
        </div>
      </section>

      <section class="section-card">
        <div class="section-head">
          <div>
            <div class="eyebrow">Parameters</div>
            <h2>常用参数</h2>
          </div>
          <p>趋势、数据集和 SVG 图都支持轻量参数控制。这里单独列出来，避免你在文档正文里来回找。</p>
        </div>
        <div class="link-grid">
          <article class="endpoint-card">
            <div class="label">Series / Datasets</div>
            <ul class="param-list">
              <li><strong>limit</strong>：取最后 N 条记录，适合前端图表按窗口加载。</li>
              <li><strong>offset</strong>：分页读取完整 dataset 或 tables。</li>
              <li><strong>metrics</strong>：逗号分隔的指标列表，只返回需要的时间序列列。</li>
              <li><strong>granularity</strong>：支持 <code>daily</code>、<code>weekly</code>、<code>monthly</code>。</li>
            </ul>
          </article>
          <article class="endpoint-card">
            <div class="label">SVG Charts</div>
            <ul class="param-list">
              <li><strong>width / height</strong>：控制 SVG 尺寸，适合嵌入不同容器。</li>
              <li><strong>theme</strong>：支持 <code>light</code> 与 <code>transparent</code>。</li>
              <li><strong>metrics</strong>：多指标会单独归一化，用来比较趋势形状而不是绝对值。</li>
              <li><strong>limit</strong>：建议前端图表默认取 14 / 30 / 90 等固定窗口。</li>
            </ul>
          </article>
        </div>
      </section>

      <section class="section-card section-anchor" id="examples">
        <div class="section-head">
          <div>
            <div class="eyebrow">Examples</div>
            <h2>调用示例</h2>
          </div>
          <p>给其他前端项目或服务端脚本接入时，最常用的通常就是这几类：公开档案、完整 dashboard、轻量 series、完整 dataset 和 SVG 图表。</p>
        </div>
        <div class="example-grid">
          <article class="endpoint-card">
            <div class="label">Profiles / Dashboard</div>
            <pre>curl "{profiles_url}"

curl "{sample_dashboard}"</pre>
          </article>
          <article class="endpoint-card">
            <div class="label">Series / Datasets / Snapshot</div>
            <pre>curl "{sample_series}"

curl "{sample_root}/datasets/activity?limit=120"
curl "{sample_root}/snapshot/endpoints/profile"</pre>
          </article>
          <article class="endpoint-card">
            <div class="label">SVG Charts</div>
            <pre>curl "{sample_root}/charts/overview-trend.svg"

curl "{sample_chart}"</pre>
          </article>
          <article class="endpoint-card">
            <div class="label">Docs</div>
            <pre>GET {openapi_json}
GET {docs_md}</pre>
          </article>
        </div>
        <div class="footer-note"><strong>说明：</strong>如果你的目标是“尽快把数据接到别的项目里”，优先从 <code>/profiles</code> → <code>/dashboard</code> 或 <code>/series</code> 开始；如果你需要完全可控的数据结构，再切到 <code>/datasets</code> 和 <code>/tables</code>。</div>
      </section>
    </div>
  </div>
</body>
</html>"""


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


def _profile_cache_dir(profile_id: str) -> str:
    return os.path.join("profiles", profile_id, "cache")


def _ensure_profile_cache_dir(profile_id: str):
    os.makedirs(_profile_cache_dir(profile_id), exist_ok=True)


def _profile_fetch_lock_path(profile_id: str) -> str:
    return os.path.join(_profile_cache_dir(profile_id), ".fetch.lock")


def _auto_sync_state_path(profile_id: str) -> str:
    return os.path.join(_profile_cache_dir(profile_id), "auto_sync_state.json")


def _dashboard_cache_path(profile_id: str) -> str:
    return os.path.join(_profile_cache_dir(profile_id), "dashboard.json")


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
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


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
    tokens_path = os.path.join("profiles", profile_id, "auth", "tokens.json")
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
    env = os.environ.copy()
    env["FITBIT_PROFILE"] = profile_id
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
    tokens_file = os.path.join("profiles", profile_id, "auth", "tokens.json")
    print(f"[{log_prefix}] Checking tokens file: {tokens_file}")
    if not os.path.exists(tokens_file):
        return False, f'Profile {profile_id} not found. Go to Profile Management -> New Profile'

    try:
        with open(tokens_file, "r", encoding="utf-8") as handle:
            tokens = json.load(handle)
    except Exception as exc:
        return False, f"Error checking tokens: {exc}"

    if not tokens or "refresh_token" not in tokens or not tokens.get("refresh_token"):
        return False, f'Profile {profile_id} needs authorization. Go to Profile Management -> Existing Profiles -> Auth'

    refresh_result = subprocess.run(
        ["python", "auth/refresh_token.py"],
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
    return False, f"Token refresh failed: {error_msg}. Go to Profile Management -> Existing Profiles -> Auth"


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
            ["python", "fetch/fetch_all.py", "--profile", profile_id],
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
        output_lines = []
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

        error_preview = "\n".join(output_lines[-20:]).strip() or f"fetch_all.py exited with code {return_code}"
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
        _auto_sync_log("No authorized profiles available for automatic sync")
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
    lock_fd = None
    try:
        print(f"[DEBUG] Thread started for job {job_id}")
        print(f"[DEBUG] Current fetch_jobs keys at thread start: {list(fetch_jobs.keys())}")
        
        # Check if job exists at thread start
        if job_id not in fetch_jobs:
            print(f"[DEBUG] ERROR: Job {job_id} not found at thread start!")
            return

        lock_fd = _acquire_profile_fetch_lock(profile_id, f"manual-job-{job_id}")
        if lock_fd is None:
            fetch_jobs[job_id]['status'] = 'failed'
            fetch_jobs[job_id]['end_time'] = _now_iso()
            fetch_jobs[job_id]['error'] = '当前档案已有同步任务在运行。'
            return
        
        _log_fetch(job_id, f"Starting fetch operation for profile: {profile_id}")
        _log_fetch(job_id, f"Job created at: {datetime.now().isoformat()}")
        
        # Update job status
        print(f"[DEBUG] Updating job {job_id} status to running")
        if job_id not in fetch_jobs:
            print(f"[DEBUG] ERROR: Job {job_id} not found in fetch_jobs during status update!")
            print(f"[DEBUG] Available jobs: {list(fetch_jobs.keys())}")
            return
        fetch_jobs[job_id]['status'] = 'running'
        fetch_jobs[job_id]['start_time'] = datetime.now().isoformat()
        print(f"[DEBUG] Job {job_id} status updated, fetch_jobs keys: {list(fetch_jobs.keys())}")
        # Initialize progress-related fields
        fetch_jobs[job_id]['current_csv'] = None
        fetch_jobs[job_id]['start_date'] = None
        fetch_jobs[job_id]['last_date'] = None
        fetch_jobs[job_id]['progress'] = 0.0
        fetch_jobs[job_id]['current_script'] = None
        fetch_jobs[job_id]['message'] = 'Preparing fetch'
        # Throttling state (API rate limit/backoff)
        fetch_jobs[job_id]['throttle_active'] = False
        fetch_jobs[job_id]['throttle_reason'] = None
        fetch_jobs[job_id]['throttle_mmss'] = None
        fetch_jobs[job_id]['throttle_until'] = None
        
        _log_fetch(job_id, "Job state initialized - status: running")
        
        # Check if profile needs re-authorization first
        try:
            ok, error_message = _refresh_profile_tokens(profile_id, f"FETCH-{job_id}")
            if not ok:
                fetch_jobs[job_id]['status'] = 'failed'
                fetch_jobs[job_id]['end_time'] = datetime.now().isoformat()
                fetch_jobs[job_id]['error'] = error_message
                return
        except Exception as e:
            print(f"Error checking/refreshing tokens for profile {profile_id}: {e}")
            fetch_jobs[job_id]['status'] = 'failed'
            fetch_jobs[job_id]['end_time'] = datetime.now().isoformat()
            fetch_jobs[job_id]['error'] = f'Error checking tokens: {e}'
            return
        
        # Prepare command
        cmd = ['python', 'fetch/fetch_all.py']
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
        if job_id not in fetch_jobs:
            print(f"[DEBUG] ERROR: Job {job_id} not found before subprocess start!")
            print(f"[DEBUG] Current fetch_jobs keys: {list(fetch_jobs.keys())}")
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
        fetch_procs[job_id] = proc

        # Track and parse progress from child output
        output_lines: list[str] = []
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
                sd_str = fetch_jobs[job_id].get('start_date')
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
                fetch_jobs[job_id]['progress'] = progress
            except Exception:
                pass

        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.rstrip("\n")
            output_lines.append(line)
            # Debug: print what we're capturing
            print(f"[FETCH-{job_id}] CAPTURED: {line}")
            
            # Check if job still exists during output processing
            if job_id not in fetch_jobs:
                print(f"[DEBUG] ERROR: Job {job_id} disappeared during output processing!")
                print(f"[DEBUG] Current fetch_jobs keys: {list(fetch_jobs.keys())}")
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
                        fetch_jobs[job_id]['current_script'] = script_name
                        fetch_jobs[job_id]['current_csv'] = script_to_csv[script_name]
                        fetch_jobs[job_id]['message'] = f"Running {script_name}"
                except Exception:
                    pass

            # Starting range lines per script
            # Steps: Starting activity data fetch from YYYY-MM-DD
            if 'starting activity data fetch from ' in low:
                try:
                    idx = low.index('starting activity data fetch from ')
                    date_str = line[idx:].split('from',1)[1].strip().split()[0]
                    fetch_jobs[job_id]['start_date'] = date_str
                    fetch_jobs[job_id]['message'] = f"Activity from {date_str}"
                    update_progress_for(None)
                except Exception:
                    pass
            # RHR: Starting resting HR fetch from YYYY-MM-DD
            if 'starting resting hr fetch from ' in low:
                try:
                    idx = low.index('starting resting hr fetch from ')
                    date_str = line[idx:].split('from',1)[1].strip().split()[0]
                    fetch_jobs[job_id]['start_date'] = date_str
                    fetch_jobs[job_id]['message'] = f"RHR from {date_str}"
                    update_progress_for(None)
                except Exception:
                    pass
            # HRV: Starting HRV fetch from YYYY-MM-DD
            if 'starting hrv fetch from ' in low:
                try:
                    idx = low.index('starting hrv fetch from ')
                    date_str = line[idx:].split('from',1)[1].strip().split()[0]
                    fetch_jobs[job_id]['start_date'] = date_str
                    fetch_jobs[job_id]['message'] = f"HRV from {date_str}"
                    update_progress_for(None)
                except Exception:
                    pass
            # Sleep: Starting sleep data fetch from YYYY-MM-DD to YYYY-MM-DD
            if 'starting sleep data fetch from ' in low:
                try:
                    idx = low.index('starting sleep data fetch from ')
                    rest = line[idx:].split('from',1)[1].strip()
                    date_str = rest.split()[0]
                    fetch_jobs[job_id]['start_date'] = date_str
                    fetch_jobs[job_id]['message'] = f"Sleep from {date_str}"
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
                            fetch_jobs[job_id]['message'] = f"Fetching {start_candidate} → {end_candidate}"
                except Exception:
                    pass

            # Saved lines: capture CSV and last date: "Saved ... to <csv> up to YYYY-MM-DD"
            if 'saved ' in low and ' to ' in low:
                try:
                    # Try to infer CSV filename
                    parts = line.strip().split()
                    csv_tokens = [p for p in parts if p.endswith('.csv')]
                    if csv_tokens:
                        fetch_jobs[job_id]['current_csv'] = os.path.basename(csv_tokens[-1])
                except Exception:
                    pass
            if ' up to ' in low:
                try:
                    after = line.lower().split(' up to ', 1)[1]
                    end_str = after.strip().split()[0]
                    # Validate date
                    if _parse_date(end_str):
                        fetch_jobs[job_id]['last_date'] = end_str
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
                        _log_fetch(job_id, f"THROTTLE: Rate-limit headers indicate reset in {secs}s (until {until})", "THROTTLE")
                        fetch_jobs[job_id]['throttle_active'] = True
                        fetch_jobs[job_id]['throttle_reason'] = 'Header reset'
                        fetch_jobs[job_id]['throttle_until'] = until
                        fetch_jobs[job_id]['throttle_mmss'] = None
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
                        fetch_jobs[job_id]['throttle_active'] = True
                        fetch_jobs[job_id]['throttle_reason'] = 'Header reset'
                        fetch_jobs[job_id]['throttle_until'] = until
                        fetch_jobs[job_id]['throttle_mmss'] = None
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
                    fetch_jobs[job_id]['throttle_active'] = True
                    fetch_jobs[job_id]['throttle_reason'] = reason
                    fetch_jobs[job_id]['throttle_until'] = until
                    fetch_jobs[job_id]['throttle_mmss'] = None
                except Exception as e:
                    print(f"[FETCH-{job_id}] ERROR parsing top-of-hour wait: {e}")
                    pass
            # Generic countdown: "Retrying in MM:SS" - only update throttle_mmss occasionally
            if 'retrying in ' in low:
                try:
                    # Only update throttle_mmss every 10 seconds to reduce server load
                    import time
                    current_time = time.time()
                    last_update = fetch_jobs[job_id].get('_last_countdown_update', 0)
                    
                    if current_time - last_update >= 10:  # Update every 10 seconds
                        # Extract last token like 12:34
                        parts = line.strip().split()
                        mmss = None
                        for p in parts[::-1]:
                            if len(p) == 5 and p[2] == ':' and p[:2].isdigit() and p[3:].isdigit():
                                mmss = p
                                break
                        if mmss:
                            current_reason = fetch_jobs[job_id].get('throttle_reason', 'Backoff')
                            print(f"[FETCH-{job_id}] THROTTLE: {current_reason} - retrying in {mmss}")
                            fetch_jobs[job_id]['throttle_active'] = True
                            # Keep existing reason if set; otherwise generic
                            if not fetch_jobs[job_id].get('throttle_reason'):
                                fetch_jobs[job_id]['throttle_reason'] = 'Backoff'
                            fetch_jobs[job_id]['throttle_mmss'] = mmss
                            fetch_jobs[job_id]['_last_countdown_update'] = current_time
                except Exception as e:
                    print(f"[FETCH-{job_id}] ERROR parsing retry countdown: {e}")
                    pass
            # Countdown completion
            if low.strip() == 'resuming...':
                print(f"[FETCH-{job_id}] THROTTLE: Resuming after throttling period")
                fetch_jobs[job_id]['throttle_active'] = False
                fetch_jobs[job_id]['throttle_reason'] = None
                fetch_jobs[job_id]['throttle_mmss'] = None
                fetch_jobs[job_id]['throttle_until'] = None

        return_code = proc.wait()
        print(f"[FETCH-{job_id}] Process completed with return code: {return_code}")
        print(f"[FETCH-{job_id}] Job status before finalization: {fetch_jobs.get(job_id, {}).get('status', 'NOT_FOUND')}")

        # Finalize job
        out_text = "\n".join(output_lines)
        print(f"[FETCH-{job_id}] STORED OUTPUT LENGTH: {len(out_text)} characters")
        print(f"[FETCH-{job_id}] STORED OUTPUT PREVIEW: {out_text[:200]}...")
        print(f"[FETCH-{job_id}] RETURN CODE: {return_code}")
        if job_id in fetch_jobs:
            fetch_jobs[job_id]['status'] = 'completed' if return_code == 0 else 'failed'
            fetch_jobs[job_id]['end_time'] = datetime.now().isoformat()
            fetch_jobs[job_id]['return_code'] = return_code
            fetch_jobs[job_id]['output'] = out_text
            fetch_jobs[job_id]['error'] = None
            
            print(f"[FETCH-{job_id}] Job finalized with status: {fetch_jobs[job_id]['status']}")
        else:
            print(f"[FETCH-{job_id}] ERROR: Job {job_id} not found in fetch_jobs during finalization!")
        
        if return_code == 0:
            print(f"[FETCH-{job_id}] SUCCESS: Fetch completed successfully for profile {profile_id}")
        else:
            print(f"[FETCH-{job_id}] ERROR: Fetch failed for profile {profile_id} with exit code {return_code}")

    except subprocess.TimeoutExpired:
        print(f"[FETCH-{job_id}] TIMEOUT: Script execution timed out after 5 minutes")
        if job_id in fetch_jobs:
            fetch_jobs[job_id]['status'] = 'timeout'
            fetch_jobs[job_id]['end_time'] = datetime.now().isoformat()
            fetch_jobs[job_id]['error'] = 'Script execution timed out after 5 minutes'
        else:
            print(f"[FETCH-{job_id}] ERROR: Job {job_id} not found in fetch_jobs during timeout handling")
    except Exception as e:
        print(f"[FETCH-{job_id}] EXCEPTION: {str(e)}")
        print(f"[FETCH-{job_id}] Exception type: {type(e).__name__}")
        import traceback
        print(f"[FETCH-{job_id}] Traceback: {traceback.format_exc()}")
        if job_id in fetch_jobs:
            fetch_jobs[job_id]['status'] = 'error'
            fetch_jobs[job_id]['end_time'] = datetime.now().isoformat()
            fetch_jobs[job_id]['error'] = str(e)
        else:
            print(f"[FETCH-{job_id}] ERROR: Job {job_id} not found in fetch_jobs during exception handling")
    finally:
        # Only clear proc handle if job is actually completed/failed
        try:
            if fetch_jobs.get(job_id, {}).get('status') in ['completed', 'failed', 'timeout', 'error', 'cancelled']:
                fetch_procs.pop(job_id, None)
                
                # Schedule job cleanup after a delay to allow frontend to check final status
                def cleanup_job():
                    import time
                    time.sleep(10)  # Wait 10 seconds before removing the job
                    if job_id in fetch_jobs:
                        print(f"[DEBUG] Cleaning up completed job {job_id} after delay")
                        del fetch_jobs[job_id]
                
                threading.Thread(target=cleanup_job, daemon=True).start()
        except Exception:
            pass
        _release_profile_fetch_lock(lock_fd)

# Static file serving (maintains existing behavior)
@app.route('/')
def index():
    """Serve the unified app shell"""
    return send_from_directory('.', 'index.html')

@app.route('/favicon.ico')
def favicon():
    """Serve favicon with proper MIME type"""
    return send_file('assets/favicon.ico', mimetype='image/x-icon')

@app.route('/<path:filename>')
def static_files(filename):
    """Serve static files with proper MIME types"""
    try:
        normalized = _normalize_public_path(filename)
        if not normalized or not _is_public_static_path(normalized):
            return "File not found", 404

        # Check if file exists before trying to serve it
        if not os.path.exists(normalized):
            return "File not found", 404
            
        # Handle CSV files with proper MIME type
        if normalized.endswith('.csv'):
            return send_file(normalized, mimetype='text/csv')
        # Handle JSON files
        elif normalized.endswith('.json'):
            return send_file(normalized, mimetype='application/json')
        # Handle other static files
        else:
            return send_from_directory('.', normalized)
    except FileNotFoundError:
        return "File not found", 404
    except Exception as e:
        return f"Error serving file: {str(e)}", 500

# Profile-specific CSV serving
@app.route('/profiles/<profile_id>/csv/<filename>')
@require_admin()
def serve_profile_csv(profile_id, filename):
    """Serve CSV files from profile directories"""
    file_path = f'profiles/{profile_id}/csv/{filename}'
    if os.path.exists(file_path):
        return send_file(file_path, mimetype='text/csv')
    return "File not found", 404

# API Endpoints
@app.route('/api/admin/session')
def admin_session():
    return jsonify(_admin_session_payload())


@app.route('/api/admin/login', methods=['POST'])
def admin_login():
    if not ADMIN_AUTH_CONFIGURED:
        return _admin_error("管理员口令尚未配置，管理功能已停用。", 503, "admin_not_configured")

    data = request.get_json() or {}
    password = (data.get('password') or '').strip()
    if not password:
        return jsonify({'error': '管理员口令不能为空。'}), 400

    if not _verify_admin_password(password):
        time.sleep(0.35)
        return _admin_error("管理员口令错误。", 401, "admin_login_failed")

    _set_admin_session()
    return jsonify({
        'message': '已进入管理员模式。',
        **_admin_session_payload(),
    })


@app.route('/api/admin/logout', methods=['POST'])
@require_admin(csrf=True)
def admin_logout():
    _clear_admin_session()
    return jsonify({
        'message': '已退出管理员模式。',
        'configured': ADMIN_AUTH_CONFIGURED,
        'authenticated': False,
        'csrf_token': None,
    })


@app.route('/api/create-profile', methods=['POST'])
@require_admin(csrf=True)
def create_profile():
    """Create a new profile with client credentials"""
    try:
        data = request.get_json()
        profile_name = data.get('profileName', '').strip()
        client_id = data.get('clientId', '').strip()
        client_secret = data.get('clientSecret', '').strip()
        
        # Validate inputs
        if not profile_name or not client_id or not client_secret:
            return jsonify({'error': 'All fields are required'}), 400
        
        # Validate profile name (alphanumeric, hyphens, underscores only)
        import re
        if not re.match(r'^[a-zA-Z0-9_-]+$', profile_name):
            return jsonify({'error': 'Profile name can only contain letters, numbers, hyphens, and underscores'}), 400
        
        # Check if profile already exists
        profile_dir = f'profiles/{profile_name}'
        if os.path.exists(profile_dir):
            return jsonify({'error': f'Profile "{profile_name}" already exists'}), 400
        
        # Create profile directory structure
        try:
            os.makedirs(f'{profile_dir}/auth', exist_ok=True)
            os.makedirs(f'{profile_dir}/csv', exist_ok=True)
            os.makedirs(f'{profile_dir}/cache', exist_ok=True)
        except PermissionError as pe:
            # Provide a helpful message for common Docker-on-Linux bind-mount issues
            msg = (
                "Permission denied creating profile directories. If running with Docker on Linux, "
                "ensure the host 'profiles' directory is writable by the container user (uid 10001). "
                "Try one of: `sudo chown -R 10001:10001 profiles`, or set `user: \"${UID:-10001}:${GID:-10001}\"` "
                "in docker-compose.yml, or relax permissions: `chmod -R 775 profiles`."
            )
            print(f"Error creating profile (permissions): {pe}")
            return jsonify({
                'error': 'Failed to create profile: permission denied',
                'hint': msg
            }), 500
        
        # Save client credentials with creation timestamp
        client_creds = {
            'client_id': client_id,
            'client_secret': client_secret,
            'created_at': datetime.now().isoformat()
        }
        
        with open(f'{profile_dir}/auth/client.json', 'w') as f:
            json.dump(client_creds, f, indent=2)
        
        # Create empty tokens file
        with open(f'{profile_dir}/auth/tokens.json', 'w') as f:
            json.dump({}, f)
        
        print(f"Created profile: {profile_name}")
        return jsonify({'message': f'Profile "{profile_name}" created successfully', 'profileName': profile_name})
        
    except PermissionError as e:
        # Catch any remaining permission errors (e.g., opening files)
        print(f"Error creating profile (permissions): {e}")
        return jsonify({
            'error': 'Failed to create profile: permission denied',
            'hint': (
                "Ensure the 'profiles' directory is writable. On Docker/Linux: "
                "`sudo chown -R 10001:10001 profiles` or set `user: \"${UID:-10001}:${GID:-10001}\"` in docker-compose.yml."
            )
        }), 500
    except Exception as e:
        print(f"Error creating profile: {e}")
        return jsonify({'error': f'Failed to create profile: {str(e)}'}), 500

@app.route('/api/delete-profile', methods=['POST'])
@require_admin(csrf=True)
def delete_profile():
    """Delete a specific profile using the reset script"""
    try:
        data = request.get_json()
        profile_name = data.get('profileName', '').strip()
        
        if not profile_name:
            return jsonify({'error': 'Profile name is required'}), 400
        
        # Validate profile name (alphanumeric, hyphens, underscores only)
        import re
        if not re.match(r'^[a-zA-Z0-9_-]+$', profile_name):
            return jsonify({'error': 'Invalid profile name format'}), 400
        
        # Check if profile exists
        profile_dir = f'profiles/{profile_name}'
        if not os.path.exists(profile_dir):
            return jsonify({'error': f'Profile "{profile_name}" not found'}), 404
        
        # Cancel any running or queued fetch jobs for this profile to avoid recreation during deletion
        try:
            to_cancel = []
            for jid, job in list(fetch_jobs.items()):
                if job.get('profile') == profile_name and job.get('status') in ('queued', 'running'):
                    to_cancel.append(jid)
            for jid in to_cancel:
                proc = fetch_procs.get(jid)
                if proc and proc.poll() is None:
                    try:
                        proc.terminate()
                        # Give it a moment to exit
                        try:
                            proc.wait(timeout=5)
                        except Exception:
                            proc.kill()
                    except Exception as e:
                        print(f"Warning: failed to terminate fetch job {jid} for profile {profile_name}: {e}")
                # Mark job as cancelled
                try:
                    fetch_jobs[jid]['status'] = 'cancelled'
                    fetch_jobs[jid]['end_time'] = datetime.now().isoformat()
                    fetch_jobs[jid]['error'] = 'Cancelled due to profile deletion'
                except Exception:
                    pass
        except Exception as e:
            print(f"Warning: error while cancelling fetch jobs for {profile_name}: {e}")
        
        # Run the reset script with --profile parameter in non-interactive mode
        import subprocess
        result = subprocess.run(
            ['python', 'reset.py', '--profile', profile_name, '--yes'],
            cwd=os.getcwd(),
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=30
        )
        
        if result.returncode == 0:
            print(f"Successfully deleted profile: {profile_name}")
            # After deletion, try to sync profiles/index.json
            try:
                import sys
                sys.path.append('auth')
                from authorize_fitbit import sync_existing_profiles  # type: ignore
                sync_existing_profiles()
            except Exception as e:
                print(f"Warning: could not sync profiles/index.json after delete: {e}")
            return jsonify({'message': f'Profile "{profile_name}" deleted successfully'})
        else:
            error_msg = result.stderr or result.stdout or 'Unknown error'
            print(f"Failed to delete profile {profile_name}: {error_msg}")
            return jsonify({'error': f'Failed to delete profile: {error_msg}'}), 500
        
    except subprocess.TimeoutExpired:
        return jsonify({'error': 'Profile deletion timed out'}), 500
    except Exception as e:
        print(f"Error deleting profile: {e}")
        return jsonify({'error': f'Failed to delete profile: {str(e)}'}), 500

@app.route('/api/fetch-data', methods=['POST'])
@require_admin(csrf=True)
def fetch_data():
    """Start a data fetch operation"""
    global job_counter
    
    data = request.get_json() or {}
    profile_id = data.get('profile', None)
    
    # Create new job
    job_counter += 1
    job_id = str(job_counter)
    
    print(f"[DEBUG] Creating job {job_id} for profile {profile_id}")
    print(f"[DEBUG] Current fetch_jobs keys: {list(fetch_jobs.keys())}")
    
    # Check if job already exists (shouldn't happen, but just in case)
    if job_id in fetch_jobs:
        print(f"[DEBUG] WARNING: Job {job_id} already exists! Overwriting...")
    
    fetch_jobs[job_id] = {
        'id': job_id,
        'profile': profile_id,
        'status': 'queued',
        'created_time': datetime.now().isoformat(),
        'start_time': None,
        'end_time': None,
        'return_code': None,
        'output': None,
        'error': None
    }
    
    print(f"[DEBUG] Job {job_id} created successfully")
    print(f"[DEBUG] Updated fetch_jobs keys: {list(fetch_jobs.keys())}")
    
    # Add periodic job existence check
    def check_job_exists():
        if job_id in fetch_jobs:
            print(f"[DEBUG] Job {job_id} still exists in fetch_jobs")
        else:
            print(f"[DEBUG] WARNING: Job {job_id} missing from fetch_jobs!")
            print(f"[DEBUG] Current fetch_jobs keys: {list(fetch_jobs.keys())}")
    
    # Check job existence after a short delay
    import threading
    def delayed_check():
        import time
        time.sleep(2)
        check_job_exists()
    
    # Add periodic monitoring to track when job disappears
    def monitor_job():
        import time
        for i in range(30):  # Monitor for 30 seconds
            time.sleep(1)
            if job_id not in fetch_jobs:
                print(f"[DEBUG] MONITOR: Job {job_id} disappeared after {i+1} seconds!")
                print(f"[DEBUG] MONITOR: Current fetch_jobs keys: {list(fetch_jobs.keys())}")
                break
            else:
                print(f"[DEBUG] MONITOR: Job {job_id} still exists after {i+1} seconds")
    
    threading.Thread(target=delayed_check, daemon=True).start()
    threading.Thread(target=monitor_job, daemon=True).start()
    
    # Start background thread
    thread = threading.Thread(target=run_fetch_script, args=(profile_id, job_id))
    thread.daemon = False  # Changed from True to False to prevent premature cleanup
    thread.start()
    
    return jsonify({
        'job_id': job_id,
        'status': 'queued',
        'message': 'Fetch operation started'
    })

@app.route('/api/fetch-status/<job_id>')
@require_admin()
def fetch_status(job_id):
    """Get status of a fetch operation"""
    # Check if fetch_jobs has been reassigned
    check_fetch_jobs_reassignment()
    
    print(f"[DEBUG] Fetch status requested for job {job_id}")
    print(f"[DEBUG] Current fetch_jobs keys: {list(fetch_jobs.keys())}")
    print(f"[DEBUG] Job {job_id} in fetch_jobs: {job_id in fetch_jobs}")
    
    if job_id not in fetch_jobs:
        print(f"[DEBUG] Job {job_id} not found in fetch_jobs. Available jobs: {list(fetch_jobs.keys())}")
        # Check if there are any jobs at all
        if not fetch_jobs:
            print(f"[DEBUG] fetch_jobs is completely empty!")
        return jsonify({'error': 'Job not found'}), 404
    
    job = fetch_jobs[job_id]
    print(f"[DEBUG] Job {job_id} status: {job.get('status', 'unknown')}, throttle_active: {job.get('throttle_active', False)}")
    return jsonify(job)

@app.route('/api/fetch-jobs')
@require_admin()
def list_fetch_jobs():
    """List all fetch jobs"""
    return jsonify(list(fetch_jobs.values()))

@app.route('/api/cancel-fetch/<job_id>', methods=['POST'])
@require_admin(csrf=True)
def cancel_fetch(job_id):
    """Cancel a running fetch operation"""
    print(f"[DEBUG] Cancel request for job {job_id}")
    print(f"[DEBUG] Current fetch_jobs keys: {list(fetch_jobs.keys())}")
    
    if job_id not in fetch_jobs:
        print(f"[DEBUG] Job {job_id} not found for cancellation")
        return jsonify({'error': 'Job not found'}), 404
    
    job = fetch_jobs[job_id]
    print(f"[DEBUG] Job {job_id} status: {job.get('status', 'unknown')}")
    
    if job['status'] not in ('queued', 'running'):
        print(f"[DEBUG] Job {job_id} cannot be cancelled (status: {job['status']})")
        return jsonify({'error': 'Job cannot be cancelled'}), 400
    
    try:
        # Terminate the subprocess if it exists
        proc = fetch_procs.get(job_id)
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                # Give it a moment to exit gracefully
                try:
                    proc.wait(timeout=5)
                except Exception:
                    proc.kill()
            except Exception as e:
                print(f"Warning: failed to terminate fetch job {job_id}: {e}")
        
        # Mark job as cancelled
        fetch_jobs[job_id]['status'] = 'cancelled'
        fetch_jobs[job_id]['end_time'] = datetime.now().isoformat()
        fetch_jobs[job_id]['error'] = 'Cancelled by user'
        
        print(f"[DEBUG] Job {job_id} marked as cancelled")
        print(f"[DEBUG] Updated fetch_jobs keys: {list(fetch_jobs.keys())}")
        
        return jsonify({
            'success': True,
            'message': 'Fetch operation cancelled'
        })
        
    except Exception as e:
        return jsonify({'error': f'Failed to cancel job: {str(e)}'}), 500

@app.route('/api/fetch-logging', methods=['GET', 'POST'])
@require_admin(csrf=True)
def fetch_logging():
    """Get or set verbose fetch logging status"""
    global VERBOSE_FETCH_LOGGING
    
    if request.method == 'GET':
        return jsonify({
            'verbose_logging': VERBOSE_FETCH_LOGGING,
            'message': 'Verbose fetch logging is ' + ('enabled' if VERBOSE_FETCH_LOGGING else 'disabled')
        })
    
    elif request.method == 'POST':
        data = request.get_json() or {}
        enabled = data.get('enabled', True)
        VERBOSE_FETCH_LOGGING = bool(enabled)
        
        return jsonify({
            'success': True,
            'verbose_logging': VERBOSE_FETCH_LOGGING,
            'message': 'Verbose fetch logging ' + ('enabled' if VERBOSE_FETCH_LOGGING else 'disabled')
        })

@app.route('/api/profiles')
def list_profiles():
    """List available profiles with creation dates"""
    profiles = []
    profiles_dir = 'profiles'
    
    if os.path.exists(profiles_dir):
        for entry in os.listdir(profiles_dir):
            profile_path = os.path.join(profiles_dir, entry)
            if os.path.isdir(profile_path) and os.path.exists(os.path.join(profile_path, 'auth', 'tokens.json')):
                # Try to get creation date from client.json
                client_file = os.path.join(profile_path, 'auth', 'client.json')
                creation_date = 'Unknown'
                
                if os.path.exists(client_file):
                    try:
                        with open(client_file, 'r') as f:
                            client_data = json.load(f)
                            if 'created_at' in client_data:
                                # Parse ISO format and format for display
                                created_dt = datetime.fromisoformat(client_data['created_at'])
                                creation_date = created_dt.strftime('%Y-%m-%d %H:%M')
                    except Exception as e:
                        print(f"Error reading creation date for {entry}: {e}")
                
                profiles.append({
                    'name': entry,
                    'created': creation_date
                })
    
    # Sort by profile name
    profiles.sort(key=lambda x: x['name'])
    return jsonify(profiles)


@app.route('/api/dashboard/<profile_id>')
def dashboard(profile_id):
    """Return the unified dashboard cache for one profile."""
    try:
        profile_dir = os.path.join('profiles', profile_id)
        if not os.path.isdir(profile_dir):
            return jsonify({'error': f'Profile "{profile_id}" not found'}), 404
        payload = load_dashboard_cache(profile_id, rebuild_if_missing=True)
        return jsonify(payload)
    except Exception as e:
        print(f"Error building dashboard for {profile_id}: {e}")
        return jsonify({'error': f'Failed to build dashboard: {str(e)}'}), 500


@app.route('/api/profile-summaries')
def profile_summaries():
    """Return lightweight summary cards for all profiles."""
    try:
        return jsonify(build_profile_cards())
    except Exception as e:
        print(f"Error building profile summaries: {e}")
        return jsonify({'error': f'Failed to build profile summaries: {str(e)}'}), 500


@app.route(f'{PUBLIC_API_BASE_PATH}')
def public_api_index():
    base_url = request.url_root.rstrip('/')
    profiles = build_profile_cards()
    sample_profile = profiles[0].get('id') if profiles else None
    data = {
        'name': 'FitBaus Public API',
        'description': '公开只读接口，面向其他项目复用本地 Fitbit 缓存、趋势序列和 SVG 图表。',
        'docs': {
            'html': f'{base_url}{PUBLIC_API_BASE_PATH}/docs',
            'markdown': f'{base_url}{PUBLIC_API_BASE_PATH}/docs.md',
            'openapi': f'{base_url}{PUBLIC_API_BASE_PATH}/openapi.json',
        },
        'profiles': {
            'count': len(profiles),
            'href': f'{base_url}{PUBLIC_API_BASE_PATH}/profiles',
        },
        'datasets': dataset_keys(),
        'sections': section_keys(),
        'tables': table_keys(),
        'charts': svg_chart_presets(),
        'sample_profile': sample_profile,
    }
    if sample_profile:
        data['sample_links'] = _public_profile_links(base_url, sample_profile)
    return _public_json_response(
        build_envelope(
            resource='public-api-index',
            data=data,
            generated_at=_now_iso(),
        )
    )


@app.route(f'{PUBLIC_API_BASE_PATH}/docs')
def public_api_docs():
    base_url = request.url_root.rstrip('/')
    return _public_text_response(_public_api_docs_html(base_url), 'text/html', max_age=600)


@app.route(f'{PUBLIC_API_BASE_PATH}/docs.md')
def public_api_docs_markdown():
    return _public_file_response('API.md', 'text/markdown', max_age=600)


@app.route(f'{PUBLIC_API_BASE_PATH}/openapi.json')
def public_api_openapi():
    base_url = request.url_root.rstrip('/')
    return _public_json_response(build_openapi_spec(base_url), max_age=600)


@app.route(f'{PUBLIC_API_BASE_PATH}/profiles')
def public_profiles():
    base_url = request.url_root.rstrip('/')
    profiles = []
    for card in build_profile_cards():
        profile_id = str(card.get('id') or '')
        profiles.append({
            **card,
            'links': _public_profile_links(base_url, profile_id),
        })
    return _public_json_response(
        build_envelope(
            resource='profiles',
            data=profiles,
            generated_at=_now_iso(),
            meta={'count': len(profiles)},
        )
    )


@app.route(f'{PUBLIC_API_BASE_PATH}/profiles/<profile_id>')
def public_profile_summary(profile_id):
    dashboard_payload = _load_public_dashboard(profile_id)
    if dashboard_payload is None:
        return _public_api_error(f'Profile "{profile_id}" not found', 404, 'profile_not_found')
    base_url = request.url_root.rstrip('/')
    data = {
        'profile': dashboard_payload.get('profile') or {},
        'overview': dashboard_payload.get('overview') or {},
        'coverage': dashboard_payload.get('coverage') or {},
        'snapshot_status': dashboard_payload.get('snapshot_status') or {},
        'links': _public_profile_links(base_url, profile_id),
    }
    return _public_json_response(
        build_envelope(
            resource='profile-summary',
            data=data,
            profile_id=profile_id,
            generated_at=dashboard_payload.get('generated_at'),
        )
    )


@app.route(f'{PUBLIC_API_BASE_PATH}/profiles/<profile_id>/dashboard')
def public_profile_dashboard(profile_id):
    dashboard_payload = _load_public_dashboard(profile_id)
    if dashboard_payload is None:
        return _public_api_error(f'Profile "{profile_id}" not found', 404, 'profile_not_found')
    return _public_json_response(
        build_envelope(
            resource='dashboard',
            data=public_dashboard_payload(dashboard_payload),
            profile_id=profile_id,
            generated_at=dashboard_payload.get('generated_at'),
        )
    )


@app.route(f'{PUBLIC_API_BASE_PATH}/profiles/<profile_id>/overview')
def public_profile_overview(profile_id):
    dashboard_payload = _load_public_dashboard(profile_id)
    if dashboard_payload is None:
        return _public_api_error(f'Profile "{profile_id}" not found', 404, 'profile_not_found')
    return _public_json_response(
        build_envelope(
            resource='overview',
            data=dashboard_payload.get('overview') or {},
            profile_id=profile_id,
            generated_at=dashboard_payload.get('generated_at'),
        )
    )


@app.route(f'{PUBLIC_API_BASE_PATH}/profiles/<profile_id>/coverage')
def public_profile_coverage(profile_id):
    dashboard_payload = _load_public_dashboard(profile_id)
    if dashboard_payload is None:
        return _public_api_error(f'Profile "{profile_id}" not found', 404, 'profile_not_found')
    return _public_json_response(
        build_envelope(
            resource='coverage',
            data=dashboard_payload.get('coverage') or {},
            profile_id=profile_id,
            generated_at=dashboard_payload.get('generated_at'),
        )
    )


@app.route(f'{PUBLIC_API_BASE_PATH}/profiles/<profile_id>/metrics')
def public_profile_metrics(profile_id):
    dashboard_payload = _load_public_dashboard(profile_id)
    if dashboard_payload is None:
        return _public_api_error(f'Profile "{profile_id}" not found', 404, 'profile_not_found')
    metrics_payload = dashboard_payload.get('stats') or []
    return _public_json_response(
        build_envelope(
            resource='metrics',
            data=metrics_payload,
            profile_id=profile_id,
            generated_at=dashboard_payload.get('generated_at'),
            meta={'count': len(metrics_payload)},
        )
    )


@app.route(f'{PUBLIC_API_BASE_PATH}/profiles/<profile_id>/metrics/<metric_key>')
def public_profile_metric(profile_id, metric_key):
    dashboard_payload = _load_public_dashboard(profile_id)
    if dashboard_payload is None:
        return _public_api_error(f'Profile "{profile_id}" not found', 404, 'profile_not_found')
    try:
        metric_payload = build_metric_payload(dashboard_payload, metric_key)
    except KeyError:
        return _public_api_error(f'Metric "{metric_key}" not found', 404, 'metric_not_found')
    return _public_json_response(
        build_envelope(
            resource='metric',
            data=metric_payload,
            profile_id=profile_id,
            generated_at=dashboard_payload.get('generated_at'),
        )
    )


@app.route(f'{PUBLIC_API_BASE_PATH}/profiles/<profile_id>/correlations')
def public_profile_correlations(profile_id):
    dashboard_payload = _load_public_dashboard(profile_id)
    if dashboard_payload is None:
        return _public_api_error(f'Profile "{profile_id}" not found', 404, 'profile_not_found')
    correlations_payload = dashboard_payload.get('correlations') or []
    return _public_json_response(
        build_envelope(
            resource='correlations',
            data=correlations_payload,
            profile_id=profile_id,
            generated_at=dashboard_payload.get('generated_at'),
            meta={'count': len(correlations_payload)},
        )
    )


@app.route(f'{PUBLIC_API_BASE_PATH}/profiles/<profile_id>/series/<granularity>')
def public_profile_series(profile_id, granularity):
    dashboard_payload = _load_public_dashboard(profile_id)
    if dashboard_payload is None:
        return _public_api_error(f'Profile "{profile_id}" not found', 404, 'profile_not_found')
    limit = parse_int_arg(request.args.get('limit'), default=None, minimum=1, maximum=1000)
    metrics = request.args.get('metrics')
    try:
        payload, meta = build_series_payload(
            profile_id=profile_id,
            dashboard=dashboard_payload,
            granularity=granularity,
            metrics=metrics,
            limit=limit,
        )
    except KeyError:
        return _public_api_error(f'Unsupported series granularity "{granularity}"', 404, 'series_not_found')
    return _public_json_response(
        build_envelope(
            resource='series',
            data=payload,
            profile_id=profile_id,
            generated_at=dashboard_payload.get('generated_at'),
            meta=meta,
        )
    )


@app.route(f'{PUBLIC_API_BASE_PATH}/profiles/<profile_id>/datasets')
def public_profile_datasets(profile_id):
    dashboard_payload = _load_public_dashboard(profile_id)
    if dashboard_payload is None:
        return _public_api_error(f'Profile "{profile_id}" not found', 404, 'profile_not_found')
    base_url = request.url_root.rstrip('/')
    coverage = dashboard_payload.get('coverage') or {}
    datasets = []
    for dataset in dataset_keys():
        datasets.append({
            'key': dataset,
            'coverage': coverage.get(dataset),
            'href': f'{base_url}{PUBLIC_API_BASE_PATH}/profiles/{profile_id}/datasets/{dataset}',
        })
    return _public_json_response(
        build_envelope(
            resource='datasets',
            data=datasets,
            profile_id=profile_id,
            generated_at=dashboard_payload.get('generated_at'),
            meta={'count': len(datasets)},
        )
    )


@app.route(f'{PUBLIC_API_BASE_PATH}/profiles/<profile_id>/datasets/<dataset>')
def public_profile_dataset(profile_id, dataset):
    dashboard_payload = _load_public_dashboard(profile_id)
    if dashboard_payload is None:
        return _public_api_error(f'Profile "{profile_id}" not found', 404, 'profile_not_found')
    limit = parse_int_arg(request.args.get('limit'), default=200, minimum=1, maximum=1000)
    offset = parse_int_arg(request.args.get('offset'), default=0, minimum=0, maximum=100000)
    try:
        payload, meta = build_dataset_payload(
            profile_id=profile_id,
            dashboard=dashboard_payload,
            dataset=dataset,
            offset=offset or 0,
            limit=limit,
        )
    except KeyError:
        return _public_api_error(f'Unsupported dataset "{dataset}"', 404, 'dataset_not_found')
    return _public_json_response(
        build_envelope(
            resource='dataset',
            data=payload,
            profile_id=profile_id,
            generated_at=dashboard_payload.get('generated_at'),
            meta=meta,
        )
    )


@app.route(f'{PUBLIC_API_BASE_PATH}/profiles/<profile_id>/sections')
def public_profile_sections(profile_id):
    dashboard_payload = _load_public_dashboard(profile_id)
    if dashboard_payload is None:
        return _public_api_error(f'Profile "{profile_id}" not found', 404, 'profile_not_found')
    sections = []
    for section_key in section_keys():
        try:
            sections.append(build_section_payload(dashboard_payload, section_key))
        except KeyError:
            continue
    return _public_json_response(
        build_envelope(
            resource='sections',
            data=sections,
            profile_id=profile_id,
            generated_at=dashboard_payload.get('generated_at'),
            meta={'count': len(sections)},
        )
    )


@app.route(f'{PUBLIC_API_BASE_PATH}/profiles/<profile_id>/sections/<section_key>')
def public_profile_section(profile_id, section_key):
    dashboard_payload = _load_public_dashboard(profile_id)
    if dashboard_payload is None:
        return _public_api_error(f'Profile "{profile_id}" not found', 404, 'profile_not_found')
    try:
        payload = build_section_payload(dashboard_payload, section_key)
    except KeyError:
        return _public_api_error(f'Section "{section_key}" not found', 404, 'section_not_found')
    return _public_json_response(
        build_envelope(
            resource='section',
            data=payload,
            profile_id=profile_id,
            generated_at=dashboard_payload.get('generated_at'),
        )
    )


@app.route(f'{PUBLIC_API_BASE_PATH}/profiles/<profile_id>/tables')
def public_profile_tables(profile_id):
    dashboard_payload = _load_public_dashboard(profile_id)
    if dashboard_payload is None:
        return _public_api_error(f'Profile "{profile_id}" not found', 404, 'profile_not_found')
    base_url = request.url_root.rstrip('/')
    tables = dashboard_payload.get('tables') or {}
    items = []
    for table_key in table_keys():
        rows = tables.get(table_key) or []
        items.append({
            'key': table_key,
            'count': len(rows) if isinstance(rows, list) else 0,
            'href': f'{base_url}{PUBLIC_API_BASE_PATH}/profiles/{profile_id}/tables/{table_key}',
        })
    return _public_json_response(
        build_envelope(
            resource='tables',
            data=items,
            profile_id=profile_id,
            generated_at=dashboard_payload.get('generated_at'),
            meta={'count': len(items)},
        )
    )


@app.route(f'{PUBLIC_API_BASE_PATH}/profiles/<profile_id>/tables/<table_key>')
def public_profile_table(profile_id, table_key):
    dashboard_payload = _load_public_dashboard(profile_id)
    if dashboard_payload is None:
        return _public_api_error(f'Profile "{profile_id}" not found', 404, 'profile_not_found')
    limit = parse_int_arg(request.args.get('limit'), default=100, minimum=1, maximum=1000)
    offset = parse_int_arg(request.args.get('offset'), default=0, minimum=0, maximum=100000)
    try:
        payload, meta = build_table_payload(
            dashboard_payload,
            table_key,
            offset=offset or 0,
            limit=limit,
        )
    except KeyError:
        return _public_api_error(f'Table "{table_key}" not found', 404, 'table_not_found')
    return _public_json_response(
        build_envelope(
            resource='table',
            data=payload,
            profile_id=profile_id,
            generated_at=dashboard_payload.get('generated_at'),
            meta=meta,
        )
    )


@app.route(f'{PUBLIC_API_BASE_PATH}/profiles/<profile_id>/snapshot-status')
def public_profile_snapshot_status(profile_id):
    dashboard_payload = _load_public_dashboard(profile_id)
    if dashboard_payload is None:
        return _public_api_error(f'Profile "{profile_id}" not found', 404, 'profile_not_found')
    return _public_json_response(
        build_envelope(
            resource='snapshot-status',
            data=dashboard_payload.get('snapshot_status') or {},
            profile_id=profile_id,
            generated_at=dashboard_payload.get('generated_at'),
        )
    )


@app.route(f'{PUBLIC_API_BASE_PATH}/profiles/<profile_id>/snapshot')
def public_profile_snapshot(profile_id):
    dashboard_payload = _load_public_dashboard(profile_id)
    if dashboard_payload is None:
        return _public_api_error(f'Profile "{profile_id}" not found', 404, 'profile_not_found')
    payload = public_snapshot_payload(profile_id)
    return _public_json_response(
        build_envelope(
            resource='snapshot',
            data=payload,
            profile_id=profile_id,
            generated_at=dashboard_payload.get('generated_at'),
        )
    )


@app.route(f'{PUBLIC_API_BASE_PATH}/profiles/<profile_id>/snapshot/endpoints')
def public_profile_snapshot_endpoints(profile_id):
    dashboard_payload = _load_public_dashboard(profile_id)
    if dashboard_payload is None:
        return _public_api_error(f'Profile "{profile_id}" not found', 404, 'profile_not_found')
    base_url = request.url_root.rstrip('/')
    snapshot_payload = public_snapshot_payload(profile_id)
    endpoints = []
    for endpoint_key, entry in (snapshot_payload.get('endpoints') or {}).items():
        endpoints.append({
            'key': endpoint_key,
            'ok': entry.get('ok'),
            'status': entry.get('status'),
            'fetched_at': entry.get('fetched_at'),
            'label': entry.get('label'),
            'group': entry.get('group'),
            'scope': entry.get('scope'),
            'href': f'{base_url}{PUBLIC_API_BASE_PATH}/profiles/{profile_id}/snapshot/endpoints/{endpoint_key}',
        })
    return _public_json_response(
        build_envelope(
            resource='snapshot-endpoints',
            data=endpoints,
            profile_id=profile_id,
            generated_at=dashboard_payload.get('generated_at'),
            meta={'count': len(endpoints)},
        )
    )


@app.route(f'{PUBLIC_API_BASE_PATH}/profiles/<profile_id>/snapshot/endpoints/<endpoint_key>')
def public_profile_snapshot_endpoint(profile_id, endpoint_key):
    dashboard_payload = _load_public_dashboard(profile_id)
    if dashboard_payload is None:
        return _public_api_error(f'Profile "{profile_id}" not found', 404, 'profile_not_found')
    snapshot_payload = public_snapshot_payload(profile_id)
    endpoint_payload = (snapshot_payload.get('endpoints') or {}).get(endpoint_key)
    if not endpoint_payload:
        return _public_api_error(f'Snapshot endpoint "{endpoint_key}" not found', 404, 'snapshot_endpoint_not_found')
    return _public_json_response(
        build_envelope(
            resource='snapshot-endpoint',
            data=endpoint_payload,
            profile_id=profile_id,
            generated_at=dashboard_payload.get('generated_at'),
            meta={'endpoint': endpoint_key},
        )
    )


@app.route(f'{PUBLIC_API_BASE_PATH}/profiles/<profile_id>/charts/<chart_key>.svg')
def public_profile_chart_svg(profile_id, chart_key):
    dashboard_payload = _load_public_dashboard(profile_id)
    if dashboard_payload is None:
        return _public_api_error(f'Profile "{profile_id}" not found', 404, 'profile_not_found')
    metrics = request.args.get('metrics')
    granularity = request.args.get('granularity')
    limit = parse_int_arg(request.args.get('limit'), default=None, minimum=1, maximum=1000)
    width = parse_int_arg(request.args.get('width'), default=960, minimum=360, maximum=1920) or 960
    height = parse_int_arg(request.args.get('height'), default=320, minimum=220, maximum=1080) or 320
    theme = (request.args.get('theme') or 'light').strip().lower() or 'light'
    try:
        svg, meta = build_chart_svg(
            profile_id=profile_id,
            dashboard=dashboard_payload,
            chart_key=chart_key,
            metrics=metrics,
            granularity=granularity,
            limit=limit,
            width=width,
            height=height,
            theme=theme,
        )
    except KeyError:
        return _public_api_error(f'Chart "{chart_key}" not found', 404, 'chart_not_found')
    response = _public_text_response(svg, 'image/svg+xml', max_age=300)
    response.headers['X-FitBaus-Chart-Meta'] = json.dumps(meta, ensure_ascii=False)
    return response


@app.route('/api/rebuild-dashboard/<profile_id>', methods=['POST'])
@require_admin(csrf=True)
def rebuild_dashboard(profile_id):
    """Force a dashboard cache rebuild for one profile."""
    try:
        profile_dir = os.path.join('profiles', profile_id)
        if not os.path.isdir(profile_dir):
            return jsonify({'error': f'Profile "{profile_id}" not found'}), 404
        payload = build_dashboard_cache(profile_id)
        return jsonify({
            'message': f'Dashboard cache rebuilt for {profile_id}',
            'generated_at': payload.get('generated_at'),
        })
    except Exception as e:
        print(f"Error rebuilding dashboard for {profile_id}: {e}")
        return jsonify({'error': f'Failed to rebuild dashboard: {str(e)}'}), 500

@app.route('/api/health')
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'active_jobs': len([j for j in fetch_jobs.values() if j['status'] == 'running']),
        'auto_sync_enabled': AUTO_SYNC_ENABLED,
        'auto_sync_interval_seconds': AUTO_SYNC_INTERVAL_SECONDS,
        'auto_sync_scan_interval_seconds': AUTO_SYNC_SCAN_INTERVAL_SECONDS,
    })

def run_authorize_script(profile_id, job_id):
    """Run authorize_fitbit.py in background thread to complete OAuth flow"""
    try:
        auth_jobs[job_id]['status'] = 'running'
        auth_jobs[job_id]['start_time'] = datetime.now().isoformat()

        # Ensure profile directory exists (created during create-profile)
        profile_dir = f'profiles/{profile_id}'
        if not os.path.exists(profile_dir):
            auth_jobs[job_id]['status'] = 'failed'
            auth_jobs[job_id]['end_time'] = datetime.now().isoformat()
            auth_jobs[job_id]['error'] = f'Profile {profile_id} not found. Create it first.'
            return

        # Run the authorization script (opens browser locally and saves tokens)
        cmd = ['python', 'auth/authorize_fitbit.py', '--profile', profile_id]
        result = subprocess.run(
            cmd,
            cwd=os.getcwd(),
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=900  # 15 minutes to allow user interaction
        )

        auth_jobs[job_id]['status'] = 'completed' if result.returncode == 0 else 'failed'
        auth_jobs[job_id]['end_time'] = datetime.now().isoformat()
        auth_jobs[job_id]['return_code'] = result.returncode
        auth_jobs[job_id]['output'] = result.stdout
        auth_jobs[job_id]['error'] = result.stderr
    except subprocess.TimeoutExpired:
        auth_jobs[job_id]['status'] = 'timeout'
        auth_jobs[job_id]['end_time'] = datetime.now().isoformat()
        auth_jobs[job_id]['error'] = 'Authorization timed out after 15 minutes'
    except Exception as e:
        auth_jobs[job_id]['status'] = 'error'
        auth_jobs[job_id]['end_time'] = datetime.now().isoformat()
        auth_jobs[job_id]['error'] = str(e)

@app.route('/api/authorize/<profile_id>', methods=['GET', 'POST'])
@require_admin(csrf=True)
def start_authorization(profile_id):
    """
    GET: Return recommended mode and authorization URL.
         If HTTPS localhost redirect is configured but cert/key are missing, return manual mode with URL.
    POST: Start background authorization job that opens a browser and captures the callback automatically.
    """
    try:
        import sys
        sys.path.append('auth')
        from authorize_fitbit import REDIRECT_URI as DEFAULT_REDIRECT_URI  # type: ignore
        from authorize_fitbit import exchange_code_for_token  # noqa: F401 (used by other endpoint)

        # Determine redirect URI and whether HTTPS localhost is usable
        redirect_uri = os.getenv('FITBIT_REDIRECT_URI', DEFAULT_REDIRECT_URI).strip()
        needs_https_local = redirect_uri.startswith('https://localhost:') or redirect_uri.startswith('https://127.0.0.1:')
        cert = os.getenv('FITBIT_SSL_CERT', '').strip()
        key = os.getenv('FITBIT_SSL_KEY', '').strip()
        has_https_creds = bool(cert and key and os.path.exists(cert) and os.path.exists(key))

        # Load client_id for auth URL
        client_file = os.path.join('profiles', profile_id, 'auth', 'client.json')
        if not os.path.exists(client_file):
            return jsonify({'error': f'Client credentials not found for profile {profile_id}'}), 400
        with open(client_file, 'r', encoding='utf-8') as f:
            client_json = json.load(f)
        client_id = client_json.get('client_id', '').strip()
        if not client_id:
            return jsonify({'error': 'Client ID missing in client.json'}), 400

        # Build authorization URL
        from urllib.parse import urlencode
        params = {
            'client_id': client_id,
            'response_type': 'code',
            'scope': FITBIT_DASHBOARD_SCOPE_TEXT,
            'redirect_uri': redirect_uri,
        }
        auth_url = f"https://www.fitbit.com/oauth2/authorize?{urlencode(params)}"

        if request.method == 'GET':
            localhost_redirect = (
                redirect_uri.startswith('http://localhost:')
                or redirect_uri.startswith('http://127.0.0.1:')
                or redirect_uri.startswith('https://localhost:')
                or redirect_uri.startswith('https://127.0.0.1:')
            )

            # For the hosted web UI, localhost callbacks are best handled with the
            # manual flow: open Fitbit in the user's browser, then paste the final
            # redirected URL/code back into the app.
            if localhost_redirect:
                detail = 'Localhost redirect detected: use manual flow.'
                if needs_https_local and not has_https_creds:
                    detail = 'HTTPS localhost redirect without certs: use manual flow.'
                return jsonify({
                    'mode': 'manual',
                    'auth_url': auth_url,
                    'redirect_uri': redirect_uri,
                    'message': detail
                })

            return jsonify({
                'mode': 'background',
                'auth_url': auth_url,
                'redirect_uri': redirect_uri,
                'message': 'Background authorization supported.'
            })

        # POST: start background job
        global auth_job_counter
        auth_job_counter += 1
        job_id = str(auth_job_counter)

        auth_jobs[job_id] = {
            'id': job_id,
            'profile': profile_id,
            'status': 'queued',
            'created_time': datetime.now().isoformat(),
            'start_time': None,
            'end_time': None,
            'return_code': None,
            'output': None,
            'error': None
        }

        thread = threading.Thread(target=run_authorize_script, args=(profile_id, job_id))
        thread.daemon = False  # Changed from True to False to prevent premature cleanup
        thread.start()

        return jsonify({
            'job_id': job_id,
            'status': 'queued',
            'message': f'Authorization started for profile: {profile_id}'
        })
    except Exception as e:
        return jsonify({'error': f'Failed to start or query authorization: {str(e)}'}), 500

@app.route('/api/authorize-status/<job_id>')
@require_admin()
def authorize_status(job_id):
    """Get status of an authorization operation"""
    if job_id not in auth_jobs:
        return jsonify({'error': 'Job not found'}), 404
    return jsonify(auth_jobs[job_id])

@app.route('/api/authorize-exchange', methods=['POST'])
@require_admin(csrf=True)
def authorize_exchange():
    """Exchange a pasted redirect URL or code for tokens (manual flow)"""
    try:
        data = request.get_json() or {}
        profile_name = (data.get('profileName') or '').strip()
        pasted_url = (data.get('redirectUrl') or '').strip()
        pasted_code = (data.get('code') or '').strip()

        if not profile_name:
            return jsonify({'error': 'Profile name is required'}), 400

        import sys
        sys.path.append('auth')
        from authorize_fitbit import (
            extract_code_from_url,
            exchange_code_for_token,
            client_credentials_file_for,
            get_active_profile,
            REDIRECT_URI as DEFAULT_REDIRECT_URI,
        )

        # Determine code
        code = pasted_code
        if not code:
            code = extract_code_from_url(pasted_url) or ''
        code = code.strip()
        if not code:
            return jsonify({'error': 'Authorization code not found. Paste the full redirected URL or the code.'}), 400

        # Load client credentials
        cred_path = os.path.join('profiles', profile_name, 'auth', 'client.json')
        if not os.path.exists(cred_path):
            return jsonify({'error': 'Client credentials file not found for this profile'}), 400
        with open(cred_path, 'r', encoding='utf-8') as f:
            cj = json.load(f)
        client_id = (cj.get('client_id') or '').strip()
        client_secret = (cj.get('client_secret') or '').strip()
        if not client_id or not client_secret:
            return jsonify({'error': 'Client credentials are incomplete'}), 400

        redirect_uri = os.getenv('FITBIT_REDIRECT_URI', DEFAULT_REDIRECT_URI).strip()

        # Exchange and save tokens
        ok = exchange_code_for_token(code, redirect_uri, client_id, client_secret, profile_id=profile_name)
        if ok:
            return jsonify({'message': 'Authorization complete and tokens saved.'})
        return jsonify({'error': 'Token exchange failed'}), 500
    except Exception as e:
        return jsonify({'error': f'Failed to exchange code: {str(e)}'}), 500

# Error handlers
@app.errorhandler(404)
def not_found(error):
    return "Resource not found", 404

@app.errorhandler(500)
def internal_error(error):
    return "Internal server error", 500

if __name__ == '__main__':
    print("Starting FitBaus Flask Server...")
    # Determine port (default to 9000; can override via env)
    port_str = os.getenv('PORT') or os.getenv('FITBAUS_PORT') or '9000'
    try:
        port = int(port_str)
    except Exception:
        port = 9000

    print(f"Server will be available at: http://localhost:{port}")
    print(f"API endpoints available at: http://localhost:{port}/api/")
    start_auto_sync_scheduler()

    # Run the Flask app
    app.run(host='0.0.0.0', port=port, debug=False)
