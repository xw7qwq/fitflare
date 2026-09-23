"""Small in-process job registry: deploy with one threaded Gunicorn worker."""
import threading
from datetime import datetime
from uuid import uuid4

fetch_jobs = {}
fetch_procs = {}
auth_jobs = {}
lock = threading.RLock()
verbose_logging = False


def new_id():
    return uuid4().hex


def create_fetch(profile):
    with lock:
        # Keep the last 100 terminal jobs; never evict queued/running work.
        terminal = [key for key, value in fetch_jobs.items() if value.get('status') not in {'queued', 'running'}]
        for key in terminal[:-99]:
            fetch_jobs.pop(key, None)
        job_id = new_id()
        fetch_jobs[job_id] = {
            'id': job_id, 'profile': profile, 'status': 'queued',
            'created_time': datetime.now().isoformat(), 'start_time': None,
            'end_time': None, 'return_code': None, 'output': None, 'error': None,
        }
        return job_id


def log_fetch(job_id, message, level='INFO'):
    if verbose_logging:
        print(f'[{datetime.now():%H:%M:%S}] [FETCH-{job_id}] [{level}] {message}')
