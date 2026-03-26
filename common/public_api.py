from __future__ import annotations

import html
from typing import Any

from common.dashboard_cache import load_dataset_rows, load_profile_snapshot


PUBLIC_API_VERSION = "v1"
PUBLIC_API_BASE_PATH = "/api/public/v1"
PUBLIC_DASHBOARD_KEYS = (
    "generated_at",
    "profile",
    "overview",
    "coverage",
    "stats",
    "correlations",
    "charts",
    "sections",
    "tables",
    "snapshot_status",
)

DATASET_LABELS = {
    "activity": "活动原始数据",
    "sleep": "睡眠原始数据",
    "hrv": "HRV 原始数据",
    "rhr": "静息心率原始数据",
    "daily": "按日聚合数据",
    "weekly": "按周聚合数据",
    "monthly": "按月聚合数据",
}

SECTION_LABELS = {
    "activity": "活动补充摘要",
    "body": "体征摘要",
    "vitals": "生命体征摘要",
    "lifestyle": "生活方式摘要",
    "account": "账户与设备摘要",
}

TABLE_LABELS = {
    "sleep": "睡眠表",
    "activity": "活动表",
    "activity_logs": "Fitbit 活动日志表",
    "recovery": "恢复表",
    "body": "体征表",
    "vitals": "生命体征表",
    "foods": "饮食表",
    "devices": "设备表",
    "badges": "徽章表",
    "alarms": "闹钟表",
    "endpoints": "快照端点表",
}

SERIES_DIMENSIONS = {
    "daily": "date",
    "weekly": "period",
    "monthly": "period",
}

FALLBACK_METRIC_META = {
    "sleep_score": {"label": "睡眠得分", "unit": "分", "tone": "blue"},
    "sleep_hours": {"label": "睡眠时长", "unit": "小时", "tone": "amber"},
    "steps": {"label": "步数", "unit": "步", "tone": "green"},
    "active_minutes": {"label": "活跃分钟", "unit": "分钟", "tone": "teal"},
    "active_zone_minutes": {"label": "燃脂区分钟", "unit": "分钟", "tone": "red"},
    "hrv": {"label": "HRV", "unit": "ms", "tone": "blue"},
    "deep_rmssd": {"label": "深睡 RMSSD", "unit": "ms", "tone": "teal"},
    "rhr": {"label": "静息心率", "unit": "bpm", "tone": "red"},
    "calories_out": {"label": "消耗热量", "unit": "kcal", "tone": "amber"},
    "sedentary_minutes": {"label": "久坐分钟", "unit": "分钟", "tone": "amber"},
    "activity_calories": {"label": "活动热量", "unit": "kcal", "tone": "amber"},
    "lightly_active_minutes": {"label": "轻度活跃分钟", "unit": "分钟", "tone": "green"},
    "fairly_active_minutes": {"label": "中等活跃分钟", "unit": "分钟", "tone": "green"},
    "very_active_minutes": {"label": "高强度活跃分钟", "unit": "分钟", "tone": "red"},
    "minutes_asleep": {"label": "入睡分钟", "unit": "分钟", "tone": "blue"},
    "minutes_awake": {"label": "清醒分钟", "unit": "分钟", "tone": "amber"},
    "time_in_bed": {"label": "在床分钟", "unit": "分钟", "tone": "blue"},
    "efficiency": {"label": "睡眠效率", "unit": "%", "tone": "green"},
    "minutes_deep": {"label": "深睡分钟", "unit": "分钟", "tone": "teal"},
    "minutes_rem": {"label": "REM 分钟", "unit": "分钟", "tone": "blue"},
    "minutes_light": {"label": "浅睡分钟", "unit": "分钟", "tone": "amber"},
    "minutes_wake_stages": {"label": "醒着分钟", "unit": "分钟", "tone": "red"},
    "minutes_to_fall_asleep": {"label": "入睡耗时", "unit": "分钟", "tone": "amber"},
    "minutes_after_wakeup": {"label": "醒后停留", "unit": "分钟", "tone": "amber"},
    "nap_count": {"label": "小睡次数", "unit": "次", "tone": "teal"},
    "deep_pct": {"label": "深睡占比", "unit": "%", "tone": "teal"},
    "rem_pct": {"label": "REM 占比", "unit": "%", "tone": "blue"},
    "days": {"label": "覆盖天数", "unit": "天", "tone": "teal"},
}

CHART_PRESETS = {
    "series": {
        "title": "自定义趋势图",
        "subtitle": "按请求参数输出轻量 SVG",
        "granularity": "daily",
        "metrics": None,
    },
    "overview-trend": {
        "title": "综合趋势",
        "subtitle": "多指标趋势会按指标各自量纲归一化后绘制",
        "granularity": "daily",
        "metrics": ["sleep_score", "steps", "hrv", "rhr"],
    },
    "weekly-trend": {
        "title": "周平均变化",
        "subtitle": "周均值图更适合看中短期状态变化",
        "granularity": "weekly",
        "metrics": ["sleep_score", "steps", "active_minutes", "hrv"],
    },
    "sleep-trend": {
        "title": "睡眠趋势",
        "subtitle": "关注睡眠得分与睡眠时长",
        "granularity": "daily",
        "metrics": ["sleep_score", "sleep_hours", "minutes_deep", "minutes_rem"],
    },
    "activity-trend": {
        "title": "活动趋势",
        "subtitle": "关注步数、活跃分钟和燃脂区分钟",
        "granularity": "daily",
        "metrics": ["steps", "active_minutes", "active_zone_minutes", "calories_out"],
    },
}

SVG_TONE_COLORS = {
    "blue": "#1a73e8",
    "green": "#188038",
    "amber": "#f9ab00",
    "red": "#d93025",
    "teal": "#0b9fa8",
}

SVG_FALLBACK_COLORS = [
    "#1a73e8",
    "#188038",
    "#f9ab00",
    "#d93025",
    "#0b9fa8",
    "#5b6ad0",
]


def public_dashboard_payload(dashboard: dict[str, Any]) -> dict[str, Any]:
    return {
        key: dashboard.get(key)
        for key in PUBLIC_DASHBOARD_KEYS
        if key in dashboard
    }


def public_snapshot_payload(profile_id: str) -> dict[str, Any]:
    snapshot = load_profile_snapshot(profile_id)
    endpoints = snapshot.get("endpoints") or {}
    public_endpoints: dict[str, Any] = {}
    for key, entry in endpoints.items():
        if not isinstance(entry, dict):
            continue
        public_endpoints[key] = {
            "ok": entry.get("ok"),
            "status": entry.get("status"),
            "fetched_at": entry.get("fetched_at"),
            "label": entry.get("label"),
            "group": entry.get("group"),
            "scope": entry.get("scope"),
            "reason": entry.get("reason"),
            "data": entry.get("data"),
        }
    return {
        "profile_id": snapshot.get("profile_id") or profile_id,
        "saved_at": snapshot.get("saved_at"),
        "requested_scopes": snapshot.get("requested_scopes") or [],
        "granted_scopes": snapshot.get("token_scope") or [],
        "range": snapshot.get("range") or {},
        "fetch_summary": snapshot.get("fetch_summary") or {},
        "endpoints": public_endpoints,
    }


def build_envelope(
    resource: str,
    data: Any,
    profile_id: str | None = None,
    generated_at: str | None = None,
    meta: dict[str, Any] | None = None,
    links: dict[str, str] | None = None,
) -> dict[str, Any]:
    payload = {
        "api_version": PUBLIC_API_VERSION,
        "resource": resource,
        "generated_at": generated_at,
        "data": data,
    }
    if profile_id:
        payload["profile_id"] = profile_id
    if meta:
        payload["meta"] = meta
    if links:
        payload["links"] = links
    return payload


def parse_int_arg(
    raw: str | None,
    default: int | None = None,
    minimum: int = 0,
    maximum: int = 1000,
) -> int | None:
    if raw in (None, ""):
        return default
    try:
        value = int(str(raw).strip())
    except Exception:
        return default
    return max(minimum, min(maximum, value))


def paginate_list(items: list[Any], offset: int = 0, limit: int | None = None) -> tuple[list[Any], dict[str, int | None]]:
    total = len(items)
    start = max(0, offset)
    if limit is None:
        end = total
    else:
        end = min(total, start + max(0, limit))
    return items[start:end], {
        "total": total,
        "offset": start,
        "limit": limit,
        "count": max(0, end - start),
    }


def metric_meta_map(dashboard: dict[str, Any]) -> dict[str, dict[str, Any]]:
    mapping: dict[str, dict[str, Any]] = {}
    for item in dashboard.get("stats") or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        if not key:
            continue
        mapping[key] = {
            "key": key,
            "label": item.get("label") or key,
            "unit": item.get("unit"),
            "tone": item.get("tone") or FALLBACK_METRIC_META.get(key, {}).get("tone") or "blue",
        }
    for key, value in FALLBACK_METRIC_META.items():
        if key not in mapping:
            mapping[key] = {
                "key": key,
                "label": value.get("label") or key,
                "unit": value.get("unit"),
                "tone": value.get("tone") or "blue",
            }
    return mapping


def metric_descriptor(key: str, dashboard: dict[str, Any]) -> dict[str, Any]:
    mapping = metric_meta_map(dashboard)
    if key in mapping:
        return mapping[key]
    return {
        "key": key,
        "label": key.replace("_", " "),
        "unit": None,
        "tone": "blue",
    }


def available_series_metrics(rows: list[dict[str, Any]], dimension: str) -> list[str]:
    keys: set[str] = set()
    for row in rows:
        for key, value in row.items():
            if key == dimension or value is None or isinstance(value, bool):
                continue
            if isinstance(value, (int, float)):
                keys.add(key)
    return sorted(keys)


def normalize_metric_keys(
    requested: str | None,
    available: list[str],
    default: list[str] | None = None,
) -> list[str]:
    if not requested:
        return list(default or available)
    requested_keys = [part.strip() for part in requested.split(",") if part.strip()]
    selected = [key for key in requested_keys if key in available]
    return selected or list(default or available)


def dataset_keys() -> list[str]:
    return list(DATASET_LABELS.keys())


def section_keys() -> list[str]:
    return list(SECTION_LABELS.keys())


def table_keys() -> list[str]:
    return list(TABLE_LABELS.keys())


def build_dataset_payload(
    profile_id: str,
    dashboard: dict[str, Any],
    dataset: str,
    offset: int = 0,
    limit: int | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    key = str(dataset or "").strip().lower()
    if key not in DATASET_LABELS:
        raise KeyError(key)
    rows = load_dataset_rows(profile_id, key)
    sliced, page = paginate_list(rows, offset=offset, limit=limit)
    return {
        "dataset": key,
        "label": DATASET_LABELS[key],
        "columns": sorted({column for row in rows[:20] for column in row.keys()}),
        "rows": sliced,
    }, {
        **page,
        "label": DATASET_LABELS[key],
        "coverage": (dashboard.get("coverage") or {}).get(key),
    }


def build_series_payload(
    profile_id: str,
    dashboard: dict[str, Any],
    granularity: str,
    metrics: str | None = None,
    limit: int | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    key = str(granularity or "").strip().lower()
    if key not in SERIES_DIMENSIONS:
        raise KeyError(key)
    rows = load_dataset_rows(profile_id, key)
    dimension = SERIES_DIMENSIONS[key]
    available = available_series_metrics(rows, dimension)
    selected = normalize_metric_keys(metrics, available)
    if limit is not None:
        rows = rows[-limit:]
    points = [{dimension: row.get(dimension), **{metric: row.get(metric) for metric in selected}} for row in rows]
    descriptors = [metric_descriptor(metric, dashboard) for metric in selected]
    return {
        "granularity": key,
        "dimension": dimension,
        "metrics": descriptors,
        "points": points,
    }, {
        "count": len(points),
        "available_metrics": available,
    }


def build_metric_payload(dashboard: dict[str, Any], metric_key: str) -> dict[str, Any]:
    for item in dashboard.get("stats") or []:
        if isinstance(item, dict) and item.get("key") == metric_key:
            return item
    raise KeyError(metric_key)


def build_section_payload(dashboard: dict[str, Any], section_key: str) -> dict[str, Any]:
    sections = dashboard.get("sections") or {}
    if section_key not in sections:
        raise KeyError(section_key)
    return {
        "section": section_key,
        "label": SECTION_LABELS.get(section_key, section_key),
        "summary": sections.get(section_key),
    }


def build_table_payload(
    dashboard: dict[str, Any],
    table_key: str,
    offset: int = 0,
    limit: int | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    tables = dashboard.get("tables") or {}
    if table_key not in tables:
        raise KeyError(table_key)
    rows = tables.get(table_key) or []
    if not isinstance(rows, list):
        rows = []
    sliced, page = paginate_list(rows, offset=offset, limit=limit)
    return {
        "table": table_key,
        "label": TABLE_LABELS.get(table_key, table_key),
        "rows": sliced,
    }, page


def svg_chart_presets() -> list[str]:
    return list(CHART_PRESETS.keys())


def _svg_color_for_metric(metric: dict[str, Any], index: int) -> str:
    tone = str(metric.get("tone") or "").strip().lower()
    return SVG_TONE_COLORS.get(tone) or SVG_FALLBACK_COLORS[index % len(SVG_FALLBACK_COLORS)]


def _svg_empty_state(title: str, subtitle: str, width: int, height: int) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-label="{html.escape(title)}">'
        f'<rect width="{width}" height="{height}" rx="28" fill="#ffffff"/>'
        f'<text x="28" y="42" font-size="20" font-weight="700" fill="#16253d">{html.escape(title)}</text>'
        f'<text x="28" y="68" font-size="12" fill="#66748c">{html.escape(subtitle)}</text>'
        f'<text x="{width / 2:.1f}" y="{height / 2:.1f}" text-anchor="middle" font-size="14" fill="#66748c">暂无可绘制数据</text>'
        "</svg>"
    )


def render_series_svg(
    title: str,
    subtitle: str,
    dimension: str,
    points: list[dict[str, Any]],
    metrics: list[dict[str, Any]],
    width: int = 960,
    height: int = 320,
    theme: str = "light",
) -> str:
    width = max(360, min(1920, int(width)))
    height = max(220, min(1080, int(height)))
    if not points or not metrics:
        return _svg_empty_state(title, subtitle, width, height)

    transparent = str(theme or "").strip().lower() == "transparent"
    background = "transparent" if transparent else "#ffffff"
    border = "rgba(26, 115, 232, 0.12)" if transparent else "#e8eef7"
    grid = "#e7edf6"
    text = "#16253d"
    muted = "#66748c"
    plot_left = 28
    plot_right = width - 24
    plot_top = 96
    plot_bottom = height - 34
    plot_width = max(1, plot_right - plot_left)
    plot_height = max(1, plot_bottom - plot_top)

    horizontal_grid = []
    for step in range(5):
        y = plot_top + (plot_height * step / 4.0)
        horizontal_grid.append(
            f'<line x1="{plot_left}" y1="{y:.2f}" x2="{plot_right}" y2="{y:.2f}" stroke="{grid}" stroke-width="1"/>'
        )

    metric_paths: list[str] = []
    legend_items: list[str] = []
    for index, metric in enumerate(metrics):
        key = str(metric.get("key") or "").strip()
        color = _svg_color_for_metric(metric, index)
        values = []
        for point in points:
            value = point.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                values.append(float(value))
        if not values:
            continue
        minimum = min(values)
        maximum = max(values)
        span = maximum - minimum
        commands = []
        last_xy = None
        point_count = max(1, len(points) - 1)
        for point_index, point in enumerate(points):
            raw = point.get(key)
            if raw is None or not isinstance(raw, (int, float)) or isinstance(raw, bool):
                continue
            x = plot_left + (plot_width * point_index / point_count)
            if span <= 0:
                y_ratio = 0.5
            else:
                y_ratio = (float(raw) - minimum) / span
            y = plot_bottom - (plot_height * y_ratio)
            commands.append(f'{"M" if not commands else "L"} {x:.2f} {y:.2f}')
            last_xy = (x, y, raw)
        if not commands:
            continue
        metric_paths.append(
            f'<path d="{" ".join(commands)}" fill="none" stroke="{color}" stroke-width="2.6" '
            f'stroke-linecap="round" stroke-linejoin="round"/>'
        )
        if last_xy is not None:
            x, y, raw = last_xy
            metric_paths.append(
                f'<circle cx="{x:.2f}" cy="{y:.2f}" r="4.2" fill="{color}" stroke="#ffffff" stroke-width="1.8"/>'
            )
            latest_text = f'{raw:.1f}'.rstrip("0").rstrip(".")
        else:
            latest_text = "--"
        unit = metric.get("unit")
        legend_items.append(
            f'<g transform="translate({28 + (index % 3) * 210},{78 + (index // 3) * 18})">'
            f'<rect width="16" height="3" y="-8" rx="2" fill="{color}"/>'
            f'<text x="22" y="-4" font-size="12" fill="{text}">{html.escape(str(metric.get("label") or key))}</text>'
            f'<text x="22" y="10" font-size="11" fill="{muted}">{html.escape(latest_text)}{html.escape(str(unit or ""))}</text>'
            f"</g>"
        )

    if not metric_paths:
        return _svg_empty_state(title, subtitle, width, height)

    start_label = points[0].get(dimension) or "--"
    end_label = points[-1].get(dimension) or "--"
    note = "多指标图按各自量纲归一化" if len(metrics) > 1 else "单指标图使用原始趋势形状"

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-label="{html.escape(title)}">'
        f'<rect width="{width}" height="{height}" rx="28" fill="{background}" stroke="{border}" stroke-width="1"/>'
        f'<text x="28" y="34" font-size="20" font-weight="700" fill="{text}">{html.escape(title)}</text>'
        f'<text x="28" y="56" font-size="12" fill="{muted}">{html.escape(subtitle)}</text>'
        f'<text x="{width - 28}" y="34" font-size="11" fill="{muted}" text-anchor="end">{html.escape(note)}</text>'
        f'{"".join(legend_items)}'
        f'{"".join(horizontal_grid)}'
        f'{"".join(metric_paths)}'
        f'<text x="{plot_left}" y="{height - 12}" font-size="11" fill="{muted}">{html.escape(str(start_label))}</text>'
        f'<text x="{plot_right}" y="{height - 12}" font-size="11" fill="{muted}" text-anchor="end">{html.escape(str(end_label))}</text>'
        "</svg>"
    )


def build_chart_svg(
    profile_id: str,
    dashboard: dict[str, Any],
    chart_key: str,
    metrics: str | None = None,
    granularity: str | None = None,
    limit: int | None = None,
    width: int = 960,
    height: int = 320,
    theme: str = "light",
) -> tuple[str, dict[str, Any]]:
    preset_key = str(chart_key or "series").strip().lower()
    preset = CHART_PRESETS.get(preset_key)
    if not preset:
        raise KeyError(preset_key)
    resolved_granularity = str(granularity or preset.get("granularity") or "daily").strip().lower()
    series_payload, meta = build_series_payload(
        profile_id=profile_id,
        dashboard=dashboard,
        granularity=resolved_granularity,
        metrics=metrics,
        limit=limit,
    )
    if not metrics and preset.get("metrics"):
        rows = load_dataset_rows(profile_id, resolved_granularity)
        dimension = SERIES_DIMENSIONS[resolved_granularity]
        available = available_series_metrics(rows, dimension)
        selected = [key for key in preset.get("metrics") or [] if key in available]
        if limit is not None:
            rows = rows[-limit:]
        series_payload = {
            "granularity": resolved_granularity,
            "dimension": dimension,
            "metrics": [metric_descriptor(metric, dashboard) for metric in selected],
            "points": [{dimension: row.get(dimension), **{metric: row.get(metric) for metric in selected}} for row in rows],
        }
        meta = {
            **meta,
            "available_metrics": available,
            "count": len(series_payload["points"]),
        }
    svg = render_series_svg(
        title=str(preset.get("title") or "趋势图"),
        subtitle=str(preset.get("subtitle") or ""),
        dimension=str(series_payload.get("dimension") or "date"),
        points=list(series_payload.get("points") or []),
        metrics=list(series_payload.get("metrics") or []),
        width=width,
        height=height,
        theme=theme,
    )
    return svg, {
        **meta,
        "chart": preset_key,
        "granularity": resolved_granularity,
        "theme": theme,
        "width": width,
        "height": height,
    }


def build_openapi_spec(base_url: str) -> dict[str, Any]:
    server_url = (base_url or "").rstrip("/") or "/"
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "FitBaus Public API",
            "version": PUBLIC_API_VERSION,
            "description": "公开只读 API，面向其他项目复用 Fitbit 本地缓存、趋势数据和 SVG 图表。",
        },
        "servers": [{"url": server_url}],
        "paths": {
            f"{PUBLIC_API_BASE_PATH}": {
                "get": {
                    "summary": "Public API index",
                    "responses": {"200": {"description": "API index"}},
                }
            },
            f"{PUBLIC_API_BASE_PATH}/profiles": {
                "get": {
                    "summary": "List public profiles",
                    "responses": {"200": {"description": "Profile list"}},
                }
            },
            f"{PUBLIC_API_BASE_PATH}/profiles/{{profile_id}}/dashboard": {
                "get": {
                    "summary": "Get public dashboard payload",
                    "parameters": [
                        {"name": "profile_id", "in": "path", "required": True, "schema": {"type": "string"}}
                    ],
                    "responses": {"200": {"description": "Dashboard payload"}},
                }
            },
            f"{PUBLIC_API_BASE_PATH}/profiles/{{profile_id}}/datasets/{{dataset}}": {
                "get": {
                    "summary": "Get full cached dataset",
                    "parameters": [
                        {"name": "profile_id", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "dataset", "in": "path", "required": True, "schema": {"type": "string", "enum": dataset_keys()}},
                        {"name": "offset", "in": "query", "schema": {"type": "integer", "minimum": 0}},
                        {"name": "limit", "in": "query", "schema": {"type": "integer", "minimum": 1, "maximum": 1000}},
                    ],
                    "responses": {"200": {"description": "Dataset payload"}},
                }
            },
            f"{PUBLIC_API_BASE_PATH}/profiles/{{profile_id}}/series/{{granularity}}": {
                "get": {
                    "summary": "Get normalized time series",
                    "parameters": [
                        {"name": "profile_id", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "granularity", "in": "path", "required": True, "schema": {"type": "string", "enum": list(SERIES_DIMENSIONS.keys())}},
                        {"name": "metrics", "in": "query", "schema": {"type": "string"}},
                        {"name": "limit", "in": "query", "schema": {"type": "integer", "minimum": 1, "maximum": 1000}},
                    ],
                    "responses": {"200": {"description": "Series payload"}},
                }
            },
            f"{PUBLIC_API_BASE_PATH}/profiles/{{profile_id}}/snapshot": {
                "get": {
                    "summary": "Get sanitized Fitbit profile snapshot cache",
                    "parameters": [
                        {"name": "profile_id", "in": "path", "required": True, "schema": {"type": "string"}}
                    ],
                    "responses": {"200": {"description": "Snapshot payload"}},
                }
            },
            f"{PUBLIC_API_BASE_PATH}/profiles/{{profile_id}}/snapshot/endpoints/{{endpoint_key}}": {
                "get": {
                    "summary": "Get one cached Fitbit snapshot endpoint",
                    "parameters": [
                        {"name": "profile_id", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "endpoint_key", "in": "path", "required": True, "schema": {"type": "string"}},
                    ],
                    "responses": {"200": {"description": "Snapshot endpoint payload"}},
                }
            },
            f"{PUBLIC_API_BASE_PATH}/profiles/{{profile_id}}/charts/{{chart_key}}.svg": {
                "get": {
                    "summary": "Get lightweight SVG trend chart",
                    "parameters": [
                        {"name": "profile_id", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "chart_key", "in": "path", "required": True, "schema": {"type": "string", "enum": svg_chart_presets()}},
                        {"name": "metrics", "in": "query", "schema": {"type": "string"}},
                        {"name": "granularity", "in": "query", "schema": {"type": "string", "enum": list(SERIES_DIMENSIONS.keys())}},
                        {"name": "limit", "in": "query", "schema": {"type": "integer", "minimum": 1, "maximum": 1000}},
                        {"name": "width", "in": "query", "schema": {"type": "integer", "minimum": 360, "maximum": 1920}},
                        {"name": "height", "in": "query", "schema": {"type": "integer", "minimum": 220, "maximum": 1080}},
                        {"name": "theme", "in": "query", "schema": {"type": "string", "enum": ["light", "transparent"]}},
                    ],
                    "responses": {"200": {"description": "SVG chart"}},
                }
            },
        },
    }
