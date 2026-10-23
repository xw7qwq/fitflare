import test from 'node:test';
import assert from 'node:assert/strict';
import { numberOrNull, formatNumber, metricAverage, minutesToHours, escapeHtml, formatSignedDelta, formatClock, formatHoursMinutes, formatRelativeTime } from '../js/format.js';
import { selectDateWindow, weeklySeries, metricWindow, normalizeDashboard, cleanDailyRows, fillCalendar, rollingAverage, compareWindows, weekdayProfile, sleepWindow, judgeChange, summarize } from '../js/data.js';
import { chartHasData, sparklineSvg } from '../js/charts.js';

test('missing and invalid values are not zero', () => {
  for (const value of [null, undefined, '', '  ', NaN, Infinity, 'bad', false, true, [], {}]) assert.equal(numberOrNull(value), null);
  assert.equal(numberOrNull(0), 0);
  assert.equal(numberOrNull('0'), 0);
  assert.equal(numberOrNull(' 12.5 '), 12.5);
});
test('averages ignore missing values while preserving real zeros', () => {
  assert.equal(metricAverage([{ x: null }, { x: 0 }, { x: 20 }, { x: '' }], 'x'), 10);
  assert.equal(metricAverage([{ x: null }], 'x'), null);
  assert.equal(metricAverage([], 'x'), null);
});
test('number formatting handles table callbacks and invalid input', () => {
  assert.equal(formatNumber(1234, { date: '2026-09-23' }), '1,234');
  assert.equal(formatNumber(Infinity), '--');
  assert.equal(formatNumber(null), '--');
  assert.equal(formatNumber(0), '0');
  assert.equal(minutesToHours(null), null);
  assert.equal(minutesToHours(90), 1.5);
});
test('date windows use calendar days, preserve source order and include boundaries', () => {
  const rows = [{ date: '2026-09-23' }, { date: '2026-09-09' }, { date: '2026-09-10' }, { date: '2026-08-01' }];
  const original = structuredClone(rows);
  assert.deepEqual(selectDateWindow(rows, 14).map(row => row.date), ['2026-09-10', '2026-09-23']);
  assert.deepEqual(rows, original);
  assert.deepEqual(selectDateWindow([], 90), []);
});
test('seven and thirty day averages use distinct calendar windows', () => {
  const rows = [{ date: '2026-08-25', hrv: 10 }, { date: '2026-09-10', hrv: 20 }, { date: '2026-09-20', hrv: 60 }, { date: '2026-09-23', hrv: null }];
  const metric = metricWindow(rows, 'hrv');
  assert.equal(metric.avg7, 60);
  assert.equal(metric.avg30, 30);
  assert.equal(metric.delta, 30);
  assert.equal(metric.points, 3);
});
test('weekly series aggregate only supplied records and ignore nulls', () => {
  const values = weeklySeries([{ date: '2026-09-21', hrv: null, sleep_hours: 6 }, { date: '2026-09-23', hrv: 40, sleep_hours: 8 }]);
  assert.deepEqual(values, [{ period: '2026-09-21', hrv: 40, sleep_hours: 7 }]);
});
test('public models do not expose internal file paths', () => {
  const raw = { files: { dashboard_cache: '/private/cache.json' }, stats: [{ key: 'hrv', latest: null, avg7: 0 }] };
  assert.deepEqual(normalizeDashboard(raw).account.files, {});
  assert.deepEqual(normalizeDashboard(raw, true).account.files, {});
  assert.equal(normalizeDashboard(raw).stats[0].latestText, '--');
  assert.equal(normalizeDashboard(raw).stats[0].avg7Text, '0.0 ms');
});
test('chart empty states handle scalar and scatter points', () => {
  for (const points of [[null], [''], [false], [{ x: null, y: 4 }], [{ x: 2, y: Infinity }]]) assert.equal(chartHasData({ datasets: [{ data: points }] }), false);
  assert.equal(chartHasData({ datasets: [{ data: [0] }] }), true);
  assert.equal(chartHasData({ datasets: [{ data: [{ x: 0, y: 1 }] }] }), true);
});
test('HTML escaping protects dynamic text', () => {
  assert.equal(escapeHtml('<img onerror="x">&\''), '&lt;img onerror=&quot;x&quot;&gt;&amp;&#39;');
});
test('non-wear days become missing, real zeros with heart data stay', () => {
  const [off, real, flagged] = cleanDailyRows([
    { date: '2026-10-02', steps: 0, active_minutes: 0, calories_out: 1721 },
    { date: '2026-10-03', steps: 0, active_minutes: 0, rhr: 60 },
    { date: '2026-10-04', steps: null, non_wear: true },
  ]);
  assert.equal(off.non_wear, true);
  assert.equal(off.steps, null);
  assert.equal(off.calories_out, null);
  assert.equal(real.steps, 0);
  assert.equal(real.non_wear, undefined);
  assert.equal(flagged.non_wear, true);
  assert.equal(normalizeDashboard({ charts: { daily: [{ date: 'bad' }, { date: '2026-10-02', steps: 0 }] } }).daily.length, 1);
});
test('calendar fill keeps gaps as explicit missing days', () => {
  const rows = fillCalendar([{ date: '2026-10-01', steps: 5 }, { date: '2026-10-04', steps: 7 }], 5);
  assert.deepEqual(rows.map(row => row.date), ['2026-09-30', '2026-10-01', '2026-10-02', '2026-10-03', '2026-10-04']);
  assert.deepEqual(rows.map(row => Boolean(row.missing)), [true, false, true, true, false]);
  assert.deepEqual(fillCalendar([], 30), []);
});
test('rolling averages need enough real values and skip gaps', () => {
  const rows = [{ v: 1 }, { v: null }, { v: 3 }, { v: 5 }, {}];
  assert.deepEqual(rollingAverage(rows, 'v', 3, 2), [null, null, 2, 4, 4]);
});
test('window comparison and weekday profile', () => {
  const rows = Array.from({ length: 14 }, (_, index) => ({ date: `2026-10-${String(index + 1).padStart(2, '0')}`, v: index < 7 ? 10 : 20 }));
  assert.deepEqual(compareWindows(rows, 'v'), { current: 20, previous: 10, delta: 10 });
  const profile = weekdayProfile([{ date: '2026-10-19', v: 4 }, { date: '2026-10-26', v: 6 }, { date: '2026-10-25', v: 1 }], 'v');
  assert.equal(profile[0], 5);
  assert.equal(profile[6], 1);
  assert.equal(profile[3], null);
  assert.deepEqual(summarize([{ v: 2 }, { v: 4 }, { v: null }], 'v'), { count: 2, avg: 3, min: 2, max: 4, sd: Math.SQRT2 });
});
test('sleep windows use hours since the previous noon', () => {
  assert.deepEqual(sleepWindow({ date: '2026-10-23', start_time: '2026-10-22T22:30:00', end_time: '2026-10-23T07:15:00' }), [10.5, 19.25]);
  assert.deepEqual(sleepWindow({ date: '2026-10-22', start_time: '2026-10-22T02:12:00', end_time: '2026-10-22T07:24:00' }), [14.2, 19.4]);
  assert.equal(sleepWindow({ date: '2026-10-22', start_time: null, end_time: '2026-10-22T07:00:00' }), null);
  assert.equal(formatClock(10.5), '22:30');
  assert.equal(formatClock(14.2), '02:12');
  assert.equal(formatHoursMinutes(8.2), '8 小时 12 分');
  assert.equal(formatHoursMinutes(null), '--');
});
test('changes respect metric polarity', () => {
  assert.equal(judgeChange('hrv', 60, 50).tone, 'good');
  assert.equal(judgeChange('rhr', 70, 60).tone, 'bad');
  assert.equal(judgeChange('rhr', 60.5, 60).direction, 'flat');
  assert.equal(judgeChange('calories_out', 3000, 2000).tone, 'neutral');
  assert.equal(judgeChange('steps', null, 2000).delta, null);
});
test('relative time and sparkline output', () => {
  const now = Date.parse('2026-10-23T12:00:00Z');
  assert.equal(formatRelativeTime('2026-10-23T11:45:00Z', now), '15 分钟前');
  assert.equal(formatRelativeTime('2026-03-10T18:12:57Z', now), '7 个月前');
  assert.equal(formatRelativeTime(null, now), '暂无');
  assert.equal(sparklineSvg([1], '#000'), '');
  assert.match(sparklineSvg([1, null, 3, 2], '#123456'), /M3\.0 [\d.]+M/);
});
test('signed deltas never show a negative zero', () => {
  assert.equal(formatSignedDelta(-0.04, 1), '0.0');
  assert.equal(formatSignedDelta(-0.4, 0), '0');
  assert.equal(formatSignedDelta(-0.06, 1), '-0.1');
  assert.equal(formatSignedDelta(2.25, 1), '+2.3');
  assert.equal(formatSignedDelta(null), '--');
});
