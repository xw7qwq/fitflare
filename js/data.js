import { formatMetricValue, metricAverage, numberOrNull } from './format.js';

export const VIEWS = {
  overview: ['总览', '睡眠、活动与恢复趋势。'],
  sleep: ['睡眠', '查看睡眠得分、时长与阶段。'],
  activity: ['活动', '查看步数、活跃分钟与活动记录。'],
  recovery: ['恢复', '对照 HRV、静息心率与睡眠变化。'],
  body: ['体征', '体重、体脂与其他体征的缓存记录。'],
  lifestyle: ['生活', '饮食、饮水与营养记录。'],
  account: ['账户', '设备、授权范围与数据缓存。'],
  family: ['档案', '切换档案，查看各自的最新记录。'],
};
const METRIC_VIEWS = { sleep_score: 'sleep', sleep_hours: 'sleep', steps: 'activity', active_minutes: 'activity', active_zone_minutes: 'activity', calories_out: 'activity', hrv: 'recovery', rhr: 'recovery' };
const CACHE_LAYERS = {
  dashboard_cache: ['仪表盘缓存', '聚合指标、图表和记录。'],
  profile_snapshot: ['Fitbit 快照', '设备、目标与补充指标。'],
  activity_csv: ['活动历史', '活动与步数的原始缓存。'],
  sleep_csv: ['睡眠历史', '睡眠时长与阶段记录。'],
  hrv_csv: ['HRV 历史', '心率变异性记录。'],
  rhr_csv: ['静息心率历史', '静息心率记录。'],
};
export function normalizeDashboard(dashboard = {}, authenticated = false) {
  const stats = (Array.isArray(dashboard.stats) ? dashboard.stats : []).map(card => ({
    ...card, targetView: METRIC_VIEWS[card.key] || 'overview',
    latestText: formatMetricValue(card.key, card.latest, card.unit),
    avg7Text: formatMetricValue(card.key, card.avg7, card.unit),
    avg30Text: formatMetricValue(card.key, card.avg30, card.unit),
  }));
  return {
    profile: dashboard.profile || {}, overview: dashboard.overview || {},
    coverage: dashboard.coverage || {}, snapshotStatus: dashboard.snapshot_status || {},
    stats, statsByKey: Object.fromEntries(stats.map(card => [card.key, card])),
    account: {
      files: authenticated ? dashboard.files || {} : {},
      cacheLayers: Object.entries(CACHE_LAYERS).map(([key, [label, detail]]) => ({ key, label, detail })),
    },
  };
}
// Calendar-day windows are anchored to the latest cached record, not today's date.
export function selectDateWindow(rows = [], days = 30, anchor) {
  const dated = rows.filter(row => /^\d{4}-\d{2}-\d{2}$/.test(row?.date || '')).sort((a, b) => a.date.localeCompare(b.date));
  if (!dated.length) return [];
  const endMs = Date.parse(`${anchor || dated.at(-1).date}T00:00:00Z`);
  const startMs = endMs - (Math.max(1, Number(days) || 30) - 1) * 86400000;
  return dated.filter(row => { const time = Date.parse(`${row.date}T00:00:00Z`); return time >= startMs && time <= endMs; });
}
export function metricWindow(rows, key) {
  const month = selectDateWindow(rows, 30);
  const avg7 = metricAverage(selectDateWindow(month, 7), key);
  const avg30 = metricAverage(month, key);
  return { avg7, avg30, delta: avg7 === null || avg30 === null ? null : avg7 - avg30, points: month.filter(row => numberOrNull(row[key]) !== null).length };
}
export function weeklySeries(rows) {
  const groups = new Map();
  for (const row of rows) {
    const day = new Date(`${row.date}T00:00:00Z`);
    day.setUTCDate(day.getUTCDate() - (day.getUTCDay() + 6) % 7);
    const week = day.toISOString().slice(0, 10);
    if (!groups.has(week)) groups.set(week, []);
    groups.get(week).push(row);
  }
  return [...groups].map(([period, values]) => ({ period, sleep_hours: metricAverage(values, 'sleep_hours'), hrv: metricAverage(values, 'hrv') }));
}
