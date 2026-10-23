import { formatMetricValue, metricAverage, numberOrNull } from './format.js';

export const VIEWS = {
  overview: ['总览', '睡眠、活动与恢复的整体状态。'],
  sleep: ['睡眠', '时长、结构、作息与睡眠估算得分。'],
  activity: ['活动', '步数、活动强度、运动记录与累计数据。'],
  recovery: ['恢复', 'HRV 与静息心率相对个人基线的变化。'],
  body: ['体征与营养', '体重、夜间体征与饮食饮水记录。'],
  account: ['设备与数据', '设备、授权与本地数据覆盖。'],
};
// Older links used a separate lifestyle tab; it now lives inside the body view.
export const VIEW_ALIASES = { lifestyle: 'body' };
const METRIC_VIEWS = { sleep_score: 'sleep', sleep_hours: 'sleep', steps: 'activity', active_minutes: 'activity', active_zone_minutes: 'activity', calories_out: 'activity', hrv: 'recovery', rhr: 'recovery' };
// true: higher is better, false: lower is better, null: neutral.
export const METRIC_POLARITY = { sleep_hours: true, sleep_score: true, steps: true, active_minutes: true, active_zone_minutes: true, hrv: true, rhr: false, calories_out: null };
export const METRIC_LABELS = { sleep_hours: '睡眠时长', sleep_score: '睡眠估算得分', steps: '步数', active_minutes: '活跃分钟', active_zone_minutes: '燃脂区分钟', calories_out: '消耗热量', hrv: 'HRV', rhr: '静息心率' };
const WEAR_FIELDS = ['steps', 'active_minutes', 'very_active_minutes', 'fairly_active_minutes', 'lightly_active_minutes', 'active_zone_minutes'];
const NON_WEAR_CLEARED = [...WEAR_FIELDS, 'calories_out', 'activity_calories', 'sedentary_minutes'];
const DAY_MS = 86400000;

export function normalizeDashboard(dashboard = {}) {
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
    daily: cleanDailyRows(dashboard.charts?.daily),
    account: {
      files: {},
    },
  };
}
// Mirrors the backend rule so caches built before it still chart correctly:
// zero movement with no sleep or heart data means the tracker was not worn.
export function isNonWearDay(row) {
  if (row?.non_wear) return true;
  if (numberOrNull(row?.steps) !== 0 || WEAR_FIELDS.some(key => numberOrNull(row[key]))) return false;
  return ['minutes_asleep', 'sleep_hours', 'hrv', 'rhr'].every(key => numberOrNull(row[key]) === null);
}
export function cleanDailyRows(rows) {
  return (Array.isArray(rows) ? rows : []).filter(row => /^\d{4}-\d{2}-\d{2}$/.test(row?.date || '')).map(row => {
    if (!isNonWearDay(row)) return row;
    const copy = { ...row, non_wear: true };
    for (const key of NON_WEAR_CLEARED) copy[key] = null;
    return copy;
  });
}
// Calendar-day windows are anchored to the latest cached record, not today's date.
export function selectDateWindow(rows = [], days = 30, anchor) {
  const dated = rows.filter(row => /^\d{4}-\d{2}-\d{2}$/.test(row?.date || '')).sort((a, b) => a.date.localeCompare(b.date));
  if (!dated.length) return [];
  const endMs = Date.parse(`${anchor || dated.at(-1).date}T00:00:00Z`);
  const startMs = endMs - (Math.max(1, Number(days) || 30) - 1) * DAY_MS;
  return dated.filter(row => { const time = Date.parse(`${row.date}T00:00:00Z`); return time >= startMs && time <= endMs; });
}
export function shiftDate(date, days) { return new Date(Date.parse(`${date}T00:00:00Z`) + days * DAY_MS).toISOString().slice(0, 10); }
// One row per calendar day (missing days become { date, missing: true }) so charts show gaps instead of joining across them.
export function fillCalendar(rows, days, anchor) {
  const window = selectDateWindow(rows, days, anchor);
  if (!window.length) return [];
  const end = anchor || window.at(-1).date;
  const byDate = new Map(window.map(row => [row.date, row]));
  const count = Math.max(1, Number(days) || 30);
  return Array.from({ length: count }, (_, index) => {
    const date = shiftDate(end, index - count + 1);
    return byDate.get(date) || { date, missing: true };
  });
}
export function metricWindow(rows, key) {
  const month = selectDateWindow(rows, 30);
  const avg7 = metricAverage(selectDateWindow(month, 7), key);
  const avg30 = metricAverage(month, key);
  return { avg7, avg30, delta: avg7 === null || avg30 === null ? null : avg7 - avg30, points: month.filter(row => numberOrNull(row[key]) !== null).length };
}
export function summarize(rows, key) {
  const values = rows.map(row => numberOrNull(row?.[key])).filter(value => value !== null);
  if (!values.length) return { count: 0, avg: null, min: null, max: null, sd: null };
  const avg = values.reduce((sum, value) => sum + value, 0) / values.length;
  const sd = values.length > 1 ? Math.sqrt(values.reduce((sum, value) => sum + (value - avg) ** 2, 0) / (values.length - 1)) : null;
  return { count: values.length, avg, min: Math.min(...values), max: Math.max(...values), sd };
}
export function latestValue(rows, key) {
  for (let index = rows.length - 1; index >= 0; index--) {
    const value = numberOrNull(rows[index]?.[key]);
    if (value !== null) return { value, date: rows[index].date };
  }
  return { value: null, date: null };
}
// Trailing average over the previous `size` calendar rows; needs at least `min` real values.
export function rollingAverage(rows, key, size = 7, min = 3) {
  return rows.map((_, index) => {
    const values = rows.slice(Math.max(0, index - size + 1), index + 1).map(row => numberOrNull(row?.[key])).filter(value => value !== null);
    return values.length >= min ? Number((values.reduce((sum, value) => sum + value, 0) / values.length).toFixed(2)) : null;
  });
}
export function compareWindows(rows, key, size = 7) {
  if (!rows.length) return { current: null, previous: null, delta: null };
  const end = rows.at(-1).date;
  const current = metricAverage(selectDateWindow(rows, size, end), key);
  const previous = metricAverage(selectDateWindow(rows, size, shiftDate(end, -size)), key);
  return { current, previous, delta: current === null || previous === null ? null : current - previous };
}
// Monday-first average per weekday.
export function weekdayProfile(rows, key) {
  const buckets = Array.from({ length: 7 }, () => []);
  for (const row of rows) {
    const value = numberOrNull(row?.[key]);
    if (value === null) continue;
    buckets[(new Date(`${row.date}T00:00:00Z`).getUTCDay() + 6) % 7].push(value);
  }
  return buckets.map(values => values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null);
}
// Hours since noon of the previous civil day: 22:30 -> 10.5, 07:15 -> 19.25.
export function clockOffset(timestamp, date) {
  const match = /^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2})/.exec(String(timestamp || ''));
  if (!match || !date) return null;
  const time = Date.parse(`${match[1]}T${match[2]}:${match[3]}:00Z`);
  const offset = (time - Date.parse(`${shiftDate(date, -1)}T12:00:00Z`)) / 3600000;
  return offset >= 0 && offset <= 36 ? Number(offset.toFixed(2)) : null;
}
export function sleepWindow(row) {
  const start = clockOffset(row?.start_time, row?.date);
  const end = clockOffset(row?.end_time, row?.date);
  return start === null || end === null || end <= start ? null : [start, end];
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
// Change relative to a baseline, classified by the metric's polarity. `tolerance` is relative (0.03 = 3%).
export function judgeChange(key, value, baseline, tolerance = 0.03) {
  if (numberOrNull(value) === null || numberOrNull(baseline) === null || baseline === 0) return { delta: null, tone: 'neutral', direction: 'flat' };
  const delta = value - baseline;
  const direction = Math.abs(delta) <= Math.abs(baseline) * tolerance ? 'flat' : delta > 0 ? 'up' : 'down';
  const polarity = METRIC_POLARITY[key];
  const tone = direction === 'flat' || polarity == null ? 'neutral' : (direction === 'up') === polarity ? 'good' : 'bad';
  return { delta, tone, direction };
}
