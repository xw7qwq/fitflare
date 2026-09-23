import test from 'node:test';
import assert from 'node:assert/strict';
import { numberOrNull, formatNumber, metricAverage, minutesToHours, escapeHtml } from '../js/format.js';
import { selectDateWindow, weeklySeries, metricWindow, normalizeDashboard } from '../js/data.js';
import { chartHasData } from '../js/charts.js';

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
  assert.equal(normalizeDashboard(raw, true).account.files.dashboard_cache, '/private/cache.json');
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
