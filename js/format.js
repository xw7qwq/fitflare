const numberFormats = new Map();
const UNITS = { sleep_hours: '小时', sleep_score: '分', hrv: 'ms', rhr: 'bpm', calories_out: 'kcal', steps: '步', active_minutes: '分钟', active_zone_minutes: '分钟' };

export function numberOrNull(value) {
  if (typeof value !== 'number' && typeof value !== 'string') return null;
  if (typeof value === 'string' && !value.trim()) return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}
export function formatNumber(value, digits = 0) {
  const number = numberOrNull(value);
  if (number === null) return '--';
  digits = Number.isInteger(digits) && digits >= 0 && digits <= 12 ? digits : 0;
  if (!numberFormats.has(digits)) numberFormats.set(digits, new Intl.NumberFormat('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits }));
  return numberFormats.get(digits).format(number);
}
export function metricValueDigits(key) { return ['sleep_hours', 'sleep_score', 'hrv'].includes(key) ? 1 : 0; }
export function formatMetricValue(key, value, unit) {
  if (numberOrNull(value) === null) return '--';
  const suffix = UNITS[key] || unit;
  return `${formatNumber(value, metricValueDigits(key))}${suffix ? ` ${suffix}` : ''}`;
}
export function formatDetailValue(value) {
  if (value === null || value === undefined || value === '') return '--';
  return typeof value === 'number' ? formatNumber(value, Number.isInteger(value) ? 0 : 1) : String(value);
}
export function formatMinutes(value) { return numberOrNull(value) === null ? '--' : `${formatNumber(value)} 分钟`; }
export function minutesToHours(value) { return numberOrNull(value) === null ? null : Number((Number(value) / 60).toFixed(1)); }
export function formatSignedDelta(value, digits = 1) {
  const number = numberOrNull(value);
  return number === null ? '--' : `${number > 0 ? '+' : ''}${formatNumber(number, digits)}`;
}
export function metricAverage(rows, key) {
  const values = rows.map(row => numberOrNull(row?.[key])).filter(value => value !== null);
  return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null;
}
export function formatDate(value, mode = 'long') {
  if (!value) return '暂无';
  const date = new Date(`${String(value).slice(0, 10)}T00:00:00`);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat('zh-CN', mode === 'short' ? { month: 'numeric', day: 'numeric' } : { year: 'numeric', month: 'numeric', day: 'numeric' }).format(date);
}
export function formatDateTime(value) {
  if (!value) return '暂无';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat('zh-CN', { year: 'numeric', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }).format(date);
}
export function statusLabel(status) {
  return ({ queued: '排队中', running: '同步中', completed: '同步完成', failed: '同步失败', timeout: '同步超时', error: '同步出错', cancelled: '已取消' })[status] || status;
}
export function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, character => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[character]);
}
