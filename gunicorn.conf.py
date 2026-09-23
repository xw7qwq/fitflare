# Gunicorn configuration for production
import multiprocessing
import os

# Server socket
# Allow overriding port via PORT env var (defaults to 9000)
_port = os.getenv("PORT", "9000")
try:
    _port_int = int(_port)
except ValueError:
    _port_int = 9000
bind = f"0.0.0.0:{_port_int}"
backlog = 2048

# Worker processes
workers = 1  # Job status is in memory: requests must share a single registry.
worker_class = "gthread"
threads = 4
worker_connections = 1000
timeout = 300
keepalive = 2
# Avoid permission errors when the container root filesystem is not writable.
worker_tmp_dir = "/tmp"
# Gunicorn 25 enables a control socket by default; we do not use gunicornc here.
control_socket_disable = True

# Restart workers after this many requests, to prevent memory leaks
max_requests = 0  # Do not recycle the process in the middle of a background sync.
max_requests_jitter = 0

# Preload app for better performance
preload_app = False
umask = 0o077

# Logging
accesslog = "-"
errorlog = "-"
loglevel = "info"
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s"'

# Process naming
proc_name = "fitbaus"

# Security
limit_request_line = 4094
limit_request_fields = 100
limit_request_field_size = 8190

# Environment
raw_env = [
    "PYTHONIOENCODING=utf-8",
]


def post_worker_init(worker):
    try:
        from server import start_auto_sync_scheduler

        start_auto_sync_scheduler()
        worker.log.info("FitBaus auto-sync scheduler started")
    except Exception:
        worker.log.exception("Failed to start FitBaus auto-sync scheduler")


def worker_exit(server, worker):
    try:
        from server import stop_auto_sync_scheduler

        stop_auto_sync_scheduler()
        server.log.info("FitBaus auto-sync scheduler stopped")
    except Exception:
        server.log.exception("Failed to stop FitBaus auto-sync scheduler cleanly")
