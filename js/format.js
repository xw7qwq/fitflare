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
  if (number === null) return '--';
  // Round first so values like -0.04 read as "0.0" rather than "-0.0".
  const rounded = Number(number.toFixed(Number.isInteger(digits) && digits >= 0 && digits <= 12 ? digits : 0)) || 0;
  return `${rounded > 0 ? '+' : ''}${formatNumber(rounded, digits)}`;
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
export function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, character => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[character]);
}
export const WEEKDAYS = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'];
// Inverse of data.clockOffset: hours since the previous noon -> "HH:MM".
export function formatClock(offset) {
  const number = numberOrNull(offset);
  if (number === null) return '--';
  const minutes = Math.round((number + 12) * 60) % 1440;
  return `${String(Math.floor(minutes / 60)).padStart(2, '0')}:${String(minutes % 60).padStart(2, '0')}`;
}
export function formatHoursMinutes(hours) {
  const number = numberOrNull(hours);
  if (number === null) return '--';
  const total = Math.round(number * 60);
  return `${Math.floor(total / 60)} 小时 ${String(total % 60).padStart(2, '0')} 分`;
}
export function formatRelativeTime(value, now = Date.now()) {
  const time = new Date(value).getTime();
  if (!value || Number.isNaN(time)) return '暂无';
  const minutes = Math.round((now - time) / 60000);
  if (minutes < 1) return '刚刚';
  if (minutes < 60) return `${minutes} 分钟前`;
  if (minutes < 1440) return `${Math.round(minutes / 60)} 小时前`;
  if (minutes < 43200) return `${Math.floor(minutes / 1440)} 天前`;
  return `${Math.floor(minutes / 43200)} 个月前`;
}
export function formatWeekday(value) {
  const date = new Date(`${String(value).slice(0, 10)}T00:00:00Z`);
  return Number.isNaN(date.getTime()) ? '' : WEEKDAYS[(date.getUTCDay() + 6) % 7];
}
