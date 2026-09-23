"""Versioned read-only API. URLs and envelope shapes remain compatible."""
import json
from flask import Blueprint, request, jsonify, make_response, render_template, current_app
from common.api_docs import build_openapi_spec, docs_context, render_markdown
from common.public_api import (
    PUBLIC_API_BASE_PATH, build_chart_svg, build_dataset_payload, build_envelope,
    build_metric_payload, build_section_payload, build_series_payload,
    build_table_payload, dataset_keys, parse_int_arg, public_dashboard_payload,
    public_snapshot_payload, section_keys, svg_chart_presets, table_keys,
)
from .repository import load_public_dashboard as _load_public_dashboard, visible_profile_cards as build_profile_cards
from .time_utils import _now_iso
from common.profile_paths import owner_profile_id

bp = Blueprint('public_api', __name__)

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


def _public_json_response(payload: dict, status_code: int = 200):
    response = jsonify(payload)
    response.status_code = status_code
    return response


def _public_text_response(body: str, mimetype: str, status_code: int = 200):
    response = make_response(body, status_code)
    response.mimetype = mimetype
    return response


def _public_profile_links(base_url: str, profile_id: str) -> dict[str, str]:
    root = f"{base_url}{PUBLIC_API_BASE_PATH}/me"
    return {
        "self": root,
        "dashboard": f"{root}/dashboard",
        "catalog": f"{root}/catalog",
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


@bp.route(f'{PUBLIC_API_BASE_PATH}')
def public_api_index():
    base_url = request.url_root.rstrip('/')
    profiles = build_profile_cards()
    sample_profile = profiles[0].get('id') if profiles else None
    data = {
        'name': 'Fitflare Personal API',
        'description': '个人只读接口，用于读取自己的 Fitbit 缓存、趋势序列和 SVG 图表。默认需要管理员会话。',
        'docs': {
            'html': f'{base_url}{PUBLIC_API_BASE_PATH}/docs',
            'markdown': f'{base_url}{PUBLIC_API_BASE_PATH}/docs.md',
            'openapi': f'{base_url}{PUBLIC_API_BASE_PATH}/openapi.json',
        },
        'profiles': {
            'count': len(profiles),
            'href': f'{base_url}{PUBLIC_API_BASE_PATH}/me',
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


@bp.route(f'{PUBLIC_API_BASE_PATH}/docs')
def public_api_docs():
    response = _public_text_response(render_template('public_api.html', **docs_context(current_app.config['DATA_ACCESS_MODE'])), 'text/html')
    response.headers['Content-Security-Policy'] = "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    return response


@bp.route(f'{PUBLIC_API_BASE_PATH}/docs.md')
def public_api_docs_markdown():
    return _public_text_response(render_markdown(), 'text/markdown')


@bp.route(f'{PUBLIC_API_BASE_PATH}/openapi.json')
def public_api_openapi():
    return _public_json_response(build_openapi_spec(access_mode=current_app.config['DATA_ACCESS_MODE']))


@bp.route(f'{PUBLIC_API_BASE_PATH}/profiles')
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


@bp.route(f'{PUBLIC_API_BASE_PATH}/me', defaults={'profile_id': None})
@bp.route(f'{PUBLIC_API_BASE_PATH}/profiles/<profile_id>')
def public_profile_summary(profile_id):
    profile_id = profile_id or owner_profile_id()
    dashboard_payload = _load_public_dashboard(profile_id)
    if dashboard_payload is None:
        return _public_api_error(f'Profile "{profile_id}" not found', 404, 'profile_not_found')
    base_url = request.url_root.rstrip('/')
    data = {
        'profile': dashboard_payload.get('profile') or {},
        'overview': dashboard_payload.get('overview') or {},
        'coverage': dashboard_payload.get('coverage') or {},
        'data_catalog': dashboard_payload.get('data_catalog') or {},
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


@bp.route(f'{PUBLIC_API_BASE_PATH}/me/dashboard', defaults={'profile_id': None})
@bp.route(f'{PUBLIC_API_BASE_PATH}/profiles/<profile_id>/dashboard')
def public_profile_dashboard(profile_id):
    profile_id = profile_id or owner_profile_id()
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


@bp.route(f'{PUBLIC_API_BASE_PATH}/me/catalog', defaults={'profile_id': None})
@bp.route(f'{PUBLIC_API_BASE_PATH}/profiles/<profile_id>/catalog')
def public_profile_catalog(profile_id):
    profile_id = profile_id or owner_profile_id()
    dashboard_payload = _load_public_dashboard(profile_id)
    if dashboard_payload is None:
        return _public_api_error(f'Profile "{profile_id}" not found', 404, 'profile_not_found')
    catalog_payload = dashboard_payload.get('data_catalog') or {}
    domains = catalog_payload.get('domains') if isinstance(catalog_payload, dict) else []
    return _public_json_response(
        build_envelope(
            resource='catalog',
            data=catalog_payload,
            profile_id=profile_id,
            generated_at=dashboard_payload.get('generated_at'),
            meta={'count': len(domains or [])},
        )
    )


@bp.route(f'{PUBLIC_API_BASE_PATH}/me/overview', defaults={'profile_id': None})
@bp.route(f'{PUBLIC_API_BASE_PATH}/profiles/<profile_id>/overview')
def public_profile_overview(profile_id):
    profile_id = profile_id or owner_profile_id()
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


@bp.route(f'{PUBLIC_API_BASE_PATH}/me/coverage', defaults={'profile_id': None})
@bp.route(f'{PUBLIC_API_BASE_PATH}/profiles/<profile_id>/coverage')
def public_profile_coverage(profile_id):
    profile_id = profile_id or owner_profile_id()
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


@bp.route(f'{PUBLIC_API_BASE_PATH}/me/metrics', defaults={'profile_id': None})
@bp.route(f'{PUBLIC_API_BASE_PATH}/profiles/<profile_id>/metrics')
def public_profile_metrics(profile_id):
    profile_id = profile_id or owner_profile_id()
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


@bp.route(f'{PUBLIC_API_BASE_PATH}/me/metrics/<metric_key>', defaults={'profile_id': None})
@bp.route(f'{PUBLIC_API_BASE_PATH}/profiles/<profile_id>/metrics/<metric_key>')
def public_profile_metric(profile_id, metric_key):
    profile_id = profile_id or owner_profile_id()
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


@bp.route(f'{PUBLIC_API_BASE_PATH}/me/correlations', defaults={'profile_id': None})
@bp.route(f'{PUBLIC_API_BASE_PATH}/profiles/<profile_id>/correlations')
def public_profile_correlations(profile_id):
    profile_id = profile_id or owner_profile_id()
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


@bp.route(f'{PUBLIC_API_BASE_PATH}/me/series/<granularity>', defaults={'profile_id': None})
@bp.route(f'{PUBLIC_API_BASE_PATH}/profiles/<profile_id>/series/<granularity>')
def public_profile_series(profile_id, granularity):
    profile_id = profile_id or owner_profile_id()
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


@bp.route(f'{PUBLIC_API_BASE_PATH}/me/datasets', defaults={'profile_id': None})
@bp.route(f'{PUBLIC_API_BASE_PATH}/profiles/<profile_id>/datasets')
def public_profile_datasets(profile_id):
    profile_id = profile_id or owner_profile_id()
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
            'href': f'{base_url}{PUBLIC_API_BASE_PATH}/me/datasets/{dataset}',
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


@bp.route(f'{PUBLIC_API_BASE_PATH}/me/datasets/<dataset>', defaults={'profile_id': None})
@bp.route(f'{PUBLIC_API_BASE_PATH}/profiles/<profile_id>/datasets/<dataset>')
def public_profile_dataset(profile_id, dataset):
    profile_id = profile_id or owner_profile_id()
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


@bp.route(f'{PUBLIC_API_BASE_PATH}/me/sections', defaults={'profile_id': None})
@bp.route(f'{PUBLIC_API_BASE_PATH}/profiles/<profile_id>/sections')
def public_profile_sections(profile_id):
    profile_id = profile_id or owner_profile_id()
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


@bp.route(f'{PUBLIC_API_BASE_PATH}/me/sections/<section_key>', defaults={'profile_id': None})
@bp.route(f'{PUBLIC_API_BASE_PATH}/profiles/<profile_id>/sections/<section_key>')
def public_profile_section(profile_id, section_key):
    profile_id = profile_id or owner_profile_id()
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


@bp.route(f'{PUBLIC_API_BASE_PATH}/me/tables', defaults={'profile_id': None})
@bp.route(f'{PUBLIC_API_BASE_PATH}/profiles/<profile_id>/tables')
def public_profile_tables(profile_id):
    profile_id = profile_id or owner_profile_id()
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
            'href': f'{base_url}{PUBLIC_API_BASE_PATH}/me/tables/{table_key}',
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


@bp.route(f'{PUBLIC_API_BASE_PATH}/me/tables/<table_key>', defaults={'profile_id': None})
@bp.route(f'{PUBLIC_API_BASE_PATH}/profiles/<profile_id>/tables/<table_key>')
def public_profile_table(profile_id, table_key):
    profile_id = profile_id or owner_profile_id()
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


@bp.route(f'{PUBLIC_API_BASE_PATH}/me/snapshot-status', defaults={'profile_id': None})
@bp.route(f'{PUBLIC_API_BASE_PATH}/profiles/<profile_id>/snapshot-status')
def public_profile_snapshot_status(profile_id):
    profile_id = profile_id or owner_profile_id()
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


@bp.route(f'{PUBLIC_API_BASE_PATH}/me/snapshot', defaults={'profile_id': None})
@bp.route(f'{PUBLIC_API_BASE_PATH}/profiles/<profile_id>/snapshot')
def public_profile_snapshot(profile_id):
    profile_id = profile_id or owner_profile_id()
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


@bp.route(f'{PUBLIC_API_BASE_PATH}/me/snapshot/endpoints', defaults={'profile_id': None})
@bp.route(f'{PUBLIC_API_BASE_PATH}/profiles/<profile_id>/snapshot/endpoints')
def public_profile_snapshot_endpoints(profile_id):
    profile_id = profile_id or owner_profile_id()
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
            'href': f'{base_url}{PUBLIC_API_BASE_PATH}/me/snapshot/endpoints/{endpoint_key}',
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


@bp.route(f'{PUBLIC_API_BASE_PATH}/me/snapshot/endpoints/<endpoint_key>', defaults={'profile_id': None})
@bp.route(f'{PUBLIC_API_BASE_PATH}/profiles/<profile_id>/snapshot/endpoints/<endpoint_key>')
def public_profile_snapshot_endpoint(profile_id, endpoint_key):
    profile_id = profile_id or owner_profile_id()
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


@bp.route(f'{PUBLIC_API_BASE_PATH}/me/charts/<chart_key>.svg', defaults={'profile_id': None})
@bp.route(f'{PUBLIC_API_BASE_PATH}/profiles/<profile_id>/charts/<chart_key>.svg')
def public_profile_chart_svg(profile_id, chart_key):
    profile_id = profile_id or owner_profile_id()
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
    response = _public_text_response(svg, 'image/svg+xml')
    response.headers['X-FitBaus-Chart-Meta'] = json.dumps(meta, ensure_ascii=False)
    return response
