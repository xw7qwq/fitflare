import { numberOrNull, escapeHtml } from './format.js';

const charts = new Map();
const FONT = '"PingFang SC", "Microsoft YaHei", system-ui, sans-serif';

export function chartHasData(data) {
  return (data?.datasets || []).some(dataset => (dataset.data || []).some(point =>
    Array.isArray(point) ? point.every(value => numberOrNull(value) !== null)
      : point && typeof point === 'object'
        ? numberOrNull(point.x) !== null && numberOrNull(point.y) !== null
        : numberOrNull(point) !== null));
}
// Reads theme tokens at render time so light/dark switches only need a re-render.
export function token(name, fallback = '') {
  if (typeof document === 'undefined') return fallback;
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}
export function seriesColor(name) { return token(`--series-${name}`, '#3987e5'); }
export function withAlpha(color, alpha) {
  const hex = /^#([0-9a-f]{6})$/i.exec(color);
  if (!hex) return color;
  const value = parseInt(hex[1], 16);
  return `rgba(${value >> 16}, ${(value >> 8) & 255}, ${value & 255}, ${alpha})`;
}

// Horizontal reference lines and an optional band drawn behind the data; labels drawn above it.
const referencePlugin = {
  id: 'reference',
  beforeDatasetsDraw(chart, _args, options) {
    const scale = chart.scales.y;
    const area = chart.chartArea;
    if (!scale || !area) return;
    const { ctx } = chart;
    ctx.save();
    const band = options?.band;
    if (band && numberOrNull(band.from) !== null && numberOrNull(band.to) !== null) {
      const top = scale.getPixelForValue(Math.max(band.from, band.to));
      const bottom = scale.getPixelForValue(Math.min(band.from, band.to));
      ctx.fillStyle = band.color;
      ctx.fillRect(area.left, Math.max(area.top, top), area.right - area.left, Math.min(area.bottom, bottom) - Math.max(area.top, top));
    }
    for (const line of options?.lines || []) {
      if (numberOrNull(line.value) === null) continue;
      const y = scale.getPixelForValue(line.value);
      if (y < area.top || y > area.bottom) continue;
      ctx.strokeStyle = line.color;
      ctx.lineWidth = 1;
      ctx.setLineDash(line.dash ? [4, 4] : []);
      ctx.beginPath(); ctx.moveTo(area.left, y); ctx.lineTo(area.right, y); ctx.stroke();
    }
    ctx.restore();
  },
  // Labels go on top of the bars, on a surface-colored pill so they stay legible.
  afterDatasetsDraw(chart, _args, options) {
    const scale = chart.scales.y;
    const area = chart.chartArea;
    if (!scale || !area) return;
    const { ctx } = chart;
    ctx.save();
    ctx.font = `11px ${FONT}`;
    ctx.textBaseline = 'middle';
    for (const line of options?.lines || []) {
      if (!line.label || numberOrNull(line.value) === null) continue;
      const y = scale.getPixelForValue(line.value);
      if (y < area.top || y > area.bottom) continue;
      const width = ctx.measureText(line.label).width + 10;
      const x = line.align === 'left' ? area.left + 2 : area.right - width - 2;
      const top = Math.max(area.top, Math.min(y - 8, area.bottom - 16));
      ctx.fillStyle = token('--surface', '#17181a');
      ctx.globalAlpha = 0.9;
      ctx.fillRect(x, top, width, 16);
      ctx.globalAlpha = 1;
      ctx.fillStyle = line.textColor || line.color;
      ctx.textAlign = 'left';
      ctx.fillText(line.label, x + 5, top + 8);
    }
    ctx.restore();
  },
};

export function upsertChart(canvasId, config) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;
  const card = canvas.closest('.chart-card');
  let empty = card.querySelector('.chart-empty');
  if (!empty) {
    empty = document.createElement('p');
    empty.className = 'chart-empty';
    empty.setAttribute('role', 'status');
    card.append(empty);
  }
  const missingLibrary = typeof window.Chart !== 'function';
  empty.hidden = !missingLibrary && chartHasData(config.data);
  empty.textContent = missingLibrary ? '图表组件加载失败，请刷新重试。' : '当前范围没有可绘制的数据。';
  canvas.hidden = !empty.hidden;
  if (!empty.hidden) { charts.get(canvasId)?.destroy(); charts.delete(canvasId); return; }
  const text = token('--text-muted', '#8b93a1');
  const grid = token('--grid', '#262a31');
  const scales = { x: {}, y: {}, ...(config.options?.scales || {}) };
  for (const [key, scale] of Object.entries(scales)) {
    scales[key] = {
      ...scale, border: { display: false, ...scale.border },
      grid: { color: grid, drawTicks: false, ...(key === 'x' && config.type !== 'scatter' ? { display: false } : {}), ...scale.grid },
      ticks: { color: text, maxRotation: 0, autoSkip: true, maxTicksLimit: key === 'x' ? 8 : 5, padding: 6, font: { family: FONT, size: 11 }, ...scale.ticks },
      title: { color: text, font: { family: FONT, size: 11 }, ...scale.title },
    };
  }
  const multi = (config.data.datasets || []).filter(dataset => !dataset.hideInLegend).length > 1;
  const options = {
    responsive: true, maintainAspectRatio: false, animation: false,
    devicePixelRatio: Math.min(window.devicePixelRatio || 1, 2),
    interaction: { mode: config.type === 'scatter' ? 'nearest' : 'index', intersect: false },
    ...config.options, scales,
    plugins: {
      legend: {
        display: multi, align: 'start',
        labels: { color: token('--text-secondary', '#c3c7cf'), usePointStyle: true, pointStyle: 'rectRounded', boxWidth: 8, boxHeight: 8, padding: 14, font: { family: FONT, size: 12 }, filter: item => !config.data.datasets[item.datasetIndex]?.hideInLegend },
      },
      tooltip: {
        backgroundColor: token('--tooltip-bg', '#22252b'), titleColor: token('--text', '#f0efec'), bodyColor: token('--text-secondary', '#c3c7cf'),
        borderColor: token('--border', '#2c2f36'), borderWidth: 1, padding: 10, boxPadding: 4, usePointStyle: true,
        titleFont: { family: FONT, size: 12, weight: '600' }, bodyFont: { family: FONT, size: 12 },
        filter: item => numberOrNull(Array.isArray(item.raw) ? item.raw[0] : item.raw?.y ?? item.raw) !== null,
        ...config.options?.plugins?.tooltip,
      },
      reference: config.options?.plugins?.reference || {},
    },
  };
  const existing = charts.get(canvasId);
  if (existing?.config.type === config.type) {
    existing.data = config.data; existing.options = options; existing.update('none');
  } else {
    existing?.destroy(); charts.set(canvasId, new window.Chart(canvas, { ...config, options, plugins: [referencePlugin] }));
  }
}
export function resizeVisibleCharts() { for (const chart of charts.values()) if (chart.canvas.offsetParent !== null) chart.resize(); }
export function destroyAllCharts() { for (const chart of charts.values()) chart.destroy(); charts.clear(); }

// Dependency-free inline sparkline: a 2px line with an end dot; gaps stay gaps.
export function sparklineSvg(values, color, { width = 120, height = 32 } = {}) {
  const points = values.map((value, index) => [index, numberOrNull(value)]);
  const real = points.filter(([, value]) => value !== null);
  if (real.length < 2) return '';
  const min = Math.min(...real.map(([, value]) => value));
  const max = Math.max(...real.map(([, value]) => value));
  const span = max - min || 1;
  const x = index => (index / Math.max(1, values.length - 1)) * (width - 6) + 3;
  const y = value => height - 4 - ((value - min) / span) * (height - 8);
  let path = '';
  let pen = false;
  for (const [index, value] of points) {
    if (value === null) { pen = false; continue; }
    path += `${pen ? 'L' : 'M'}${x(index).toFixed(1)} ${y(value).toFixed(1)}`;
    pen = true;
  }
  const [lastIndex, lastValue] = real.at(-1);
  return `<svg class="sparkline" viewBox="0 0 ${width} ${height}" aria-hidden="true"><path d="${path}" fill="none" stroke="${escapeHtml(color)}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/><circle cx="${x(lastIndex).toFixed(1)}" cy="${y(lastValue).toFixed(1)}" r="3" fill="${escapeHtml(color)}"/></svg>`;
}
