#!/usr/bin/env python3
"""FitBaus application factory and static entry points; imports start no jobs."""
import os
from pathlib import Path, PurePosixPath
from flask import Flask, jsonify, request, send_from_directory
from backend.config import settings, AUTO_SYNC_ENABLED, AUTO_SYNC_INTERVAL_SECONDS, AUTO_SYNC_SCAN_INTERVAL_SECONDS
from backend.security import install_security, require_admin
from backend.sync import start_auto_sync_scheduler, stop_auto_sync_scheduler
from backend import jobs
from backend.time_utils import _now_iso

ROOT = Path(__file__).resolve().parent
STATIC_FILES = {
    'index.html', 'app.js', 'style.css', 'mobile.html', 'spousal.html',
    'js/format.js', 'js/data.js', 'js/charts.js', 'js/views.js', 'js/table.js',
    'docs.css', 'js/docs.js', 'js/docs-request.js',
    'vendor/chart.umd-4.4.1.min.js',
}


def create_app(config=None):
    app = Flask(__name__, static_folder=None, template_folder=str(ROOT / 'templates'))
    app.config.update(settings())
    app.config.update(config or {})
    install_security(app)
    from backend import security, dashboard_routes, public_routes, admin_routes
    for module in (security, dashboard_routes, public_routes, admin_routes):
        app.register_blueprint(module.bp)

    @app.get('/')
    def index():
        return send_from_directory(ROOT, 'index.html')

    @app.get('/favicon.ico')
    def favicon():
        return send_from_directory(ROOT / 'assets', 'favicon.ico')

    @app.get('/<path:filename>')
    def static_files(filename):
        path = PurePosixPath(filename)
        image = filename.startswith('assets/') and path.suffix.lower() in {'.png', '.jpg', '.jpeg', '.svg', '.ico', '.webp'}
        if '..' in path.parts or '\\' in filename or (filename not in STATIC_FILES and not image):
            return 'File not found', 404
        return send_from_directory(ROOT, filename)

    @app.get('/profiles/<profile_id>/csv/<filename>')
    @require_admin()
    def serve_profile_csv(profile_id, filename):
        if not filename.endswith('.csv') or Path(filename).name != filename:
            return 'File not found', 404
        directory = Path(app.config['PROFILES_DIR']) / profile_id / 'csv'
        return send_from_directory(directory, filename, mimetype='text/csv')

    @app.get('/api/health')
    def health_check():
        with jobs.lock:
            active = sum(job.get('status') == 'running' for job in jobs.fetch_jobs.values())
        return jsonify(status='healthy', timestamp=_now_iso(), active_jobs=active,
                       auto_sync_enabled=AUTO_SYNC_ENABLED,
                       auto_sync_interval_seconds=AUTO_SYNC_INTERVAL_SECONDS,
                       auto_sync_scan_interval_seconds=AUTO_SYNC_SCAN_INTERVAL_SECONDS)

    @app.after_request
    def static_cache_headers(response):
        if request.path == '/vendor/chart.umd-4.4.1.min.js' and response.status_code == 200:
            response.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
        elif request.path == '/' or request.path.lstrip('/') in STATIC_FILES:
            response.headers['Cache-Control'] = 'no-cache'
        return response

    @app.errorhandler(404)
    def not_found(error):
        return 'Resource not found', 404

    @app.errorhandler(500)
    def internal_error(error):
        return 'Internal server error', 500

    return app


app = create_app()

if __name__ == '__main__':
    start_auto_sync_scheduler()
    try:
        app.run(host='0.0.0.0', port=int(os.getenv('PORT', '9000')), debug=False)
    finally:
        stop_auto_sync_scheduler()
