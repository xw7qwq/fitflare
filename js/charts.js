import { numberOrNull } from './format.js';

const charts = new Map();
const FONT = '"PingFang SC", "Microsoft YaHei", system-ui, sans-serif';
export function chartHasData(data) {
  return (data?.datasets || []).some(dataset => (dataset.data || []).some(point =>
    point && typeof point === 'object'
      ? numberOrNull(point.x) !== null && numberOrNull(point.y) !== null
      : numberOrNull(point) !== null));
}
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
  const css = getComputedStyle(document.documentElement);
  const color = (name, fallback) => css.getPropertyValue(name).trim() || fallback;
  const text = color('--muted', '#a4b0c0');
  const scales = { x: {}, ...(config.options?.scales || {}) };
  for (const [key, scale] of Object.entries(scales)) {
    scales[key] = {
      ...scale, border: { display: false, ...scale.border },
      grid: { color: color('--border', '#283342'), drawTicks: false, ...scale.grid },
      ticks: { color: text, maxRotation: 0, autoSkip: true, maxTicksLimit: 7, ...scale.ticks },
      title: { color: text, ...scale.title },
    };
  }
  const options = {
    responsive: true, maintainAspectRatio: false, animation: false,
    devicePixelRatio: Math.min(window.devicePixelRatio || 1, 2),
    interaction: { mode: config.type === 'scatter' ? 'nearest' : 'index', intersect: false },
    ...config.options, scales,
    plugins: {
      legend: { align: 'start', labels: { color: text, usePointStyle: true, boxWidth: 8, padding: 16, font: { family: FONT } } },
      tooltip: { backgroundColor: '#1b2635', titleColor: '#f0f4f8', bodyColor: '#d4dce6', padding: 12 },
      ...config.options?.plugins,
    },
  };
  const existing = charts.get(canvasId);
  if (existing?.config.type === config.type) {
    existing.data = config.data; existing.options = options; existing.update('none');
  } else {
    existing?.destroy(); charts.set(canvasId, new window.Chart(canvas, { ...config, options }));
  }
}
export function resizeVisibleCharts() { for (const chart of charts.values()) if (chart.canvas.offsetParent !== null) chart.resize(); }
export function destroyAllCharts() { for (const chart of charts.values()) chart.destroy(); charts.clear(); }
export const dualAxisOptions = scales => ({ scales });
export const stackedOptions = unit => ({ scales: { x: { stacked: true }, y: { stacked: true, title: { display: true, text: unit } } } });
