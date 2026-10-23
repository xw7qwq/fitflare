import {
  escapeHtml, formatDate, formatDateTime, formatNumber, formatMinutes, formatMetricValue, formatSignedDelta,
  formatClock, formatHoursMinutes, formatRelativeTime, formatWeekday, metricValueDigits, numberOrNull, WEEKDAYS,
} from './format.js';
import {
  fillCalendar, summarize, latestValue, rollingAverage, compareWindows, weekdayProfile, sleepWindow,
  metricWindow, judgeChange, selectDateWindow, METRIC_LABELS,
} from './data.js';
import { upsertChart, seriesColor, withAlpha, token, sparklineSvg } from './charts.js';

const UNITS = { sleep_hours: '小时', sleep_score: '分', steps: '步', active_minutes: '分钟', active_zone_minutes: '分钟', calories_out: 'kcal', hrv: 'ms', rhr: 'bpm' };
const SERIES = { sleep_hours: 'sleep', sleep_score: 'sleep', steps: 'steps', active_minutes: 'steps', active_zone_minutes: 'zone', calories_out: 'energy', hrv: 'hrv', rhr: 'rhr' };
const KPI_KEYS = [['sleep_hours', 'sleep'], ['sleep_score', 'sleep'], ['steps', 'activity'], ['active_minutes', 'activity'], ['hrv', 'recovery'], ['rhr', 'recovery']];
const MONTHLY_KEYS = ['steps', 'sleep_hours', 'hrv', 'rhr', 'active_minutes'];
const STEP_GOAL = 10000;
const DAY = 86400000;
const SLEEP_TARGET = 7;
const ACTIVITY_NAMES = { Walk: '步行', Run: '跑步', 'Aerobic Workout': '有氧运动', 'Outdoor Bike': '户外骑行', Bike: '骑行', Swim: '游泳', Hike: '徒步', Yoga: '瑜伽', Treadmill: '跑步机', Elliptical: '椭圆机', Weights: '力量训练', 'Strength training': '力量训练', Workout: '训练', Sport: '运动', Tennis: '网球', Basketball: '篮球', Soccer: '足球' };
const SCOPE_NAMES = { 'settings.readonly': '设备与设置', 'sleep.readonly': '睡眠', 'activity_and_fitness.readonly': '活动与健身', 'health_metrics_and_measurements.readonly': '健康指标与测量', 'nutrition.readonly': '营养与饮水', 'profile.readonly': '个人资料' };
const BATTERY = { High: ['电量高', 'good'], Medium: ['电量中', 'neutral'], Low: ['电量低', 'warning'], Empty: ['电量耗尽', 'critical'] };
const TONE_ICON = { good: '▲', bad: '▼', neutral: '●', warning: '!', critical: '!' };

export function createViews({ state, refs, renderTable }) {
  const range = () => Number(refs.rangeSelect.value) || 30;
  const allDaily = () => state.viewModel?.daily || [];
  const series = () => fillCalendar(allDaily(), range());
  const unit = key => UNITS[key] || '';
  const fmt = (key, value) => formatMetricValue(key, value, unit(key));
  const shortLabels = rows => rows.map(row => formatDate(row.date, 'short'));
  const titleFor = rows => items => {
    const row = rows[items[0]?.dataIndex];
    return row ? `${formatDate(row.date)} ${formatWeekday(row.date)}${row.non_wear ? ' · 未佩戴' : row.missing ? ' · 无记录' : ''}` : '';
  };
  const tooltip = (rows, format) => ({ callbacks: { title: titleFor(rows), label: context => ` ${context.dataset.label}  ${format(context.raw, context)}` } });
  const avgLine = (value, format) => ({ value, dash: true, color: token('--text-muted'), label: value == null ? '' : `均值 ${format(value)}` });

  function kpiCard({ label, value, unitText = '', sub = '', note = '', tone, series: name, tag = 'article', attrs = '' }) {
    const dot = name ? `<i class="dot" data-series="${escapeHtml(name)}"></i>` : '';
    const noteHtml = note ? `<span class="kpi-note${tone ? ` tone-${tone}` : ''}">${tone ? `<b aria-hidden="true">${TONE_ICON[tone] || ''}</b>` : ''}${escapeHtml(note)}</span>` : '';
    return `<${tag} class="kpi-card" ${attrs}><span class="metric-label">${dot}${escapeHtml(label)}</span>
      <strong class="kpi-value">${escapeHtml(value)}${unitText ? `<small>${escapeHtml(unitText)}</small>` : ''}</strong>
      ${noteHtml}${sub ? `<span class="kpi-sub">${escapeHtml(sub)}</span>` : ''}</${tag}>`;
  }
  function splitValue(key, value) {
    if (numberOrNull(value) === null) return ['--', ''];
    return [formatNumber(value, metricValueDigits(key)), unit(key)];
  }
  function changeNote(key, value, baseline, suffix) {
    const change = judgeChange(key, value, baseline);
    if (change.delta === null) return { note: '', tone: undefined };
    const words = change.direction === 'flat' ? '持平' : change.direction === 'up' ? '高于' : '低于';
    const amount = change.direction === 'flat' ? '' : ` ${formatNumber(Math.abs(change.delta), metricValueDigits(key))} ${unit(key)}`;
    return { note: `${words}${suffix}${amount}`, tone: change.direction === 'flat' ? 'neutral' : change.tone === 'neutral' ? 'neutral' : change.tone };
  }
  function scaleFor(key) {
    return key === 'sleep_score' ? { suggestedMin: 50, suggestedMax: 100 } : key === 'hrv' || key === 'rhr' ? { grace: '10%' } : { beginAtZero: true };
  }

  // ---------- shared ----------
  function renderDataNote() {
    const rows = series();
    const nonWear = rows.filter(row => row.non_wear).map(row => row.date);
    const missing = rows.filter(row => row.missing).length;
    const parts = [];
    if (nonWear.length) parts.push(`${nonWear.length} 天未佩戴设备（${formatDate(nonWear[0], 'short')}${nonWear.length > 1 ? `–${formatDate(nonWear.at(-1), 'short')}` : ''}）`);
    if (missing) parts.push(`${missing} 天没有同步记录`);
    refs.dataQualityNote.hidden = !parts.length || !['overview', 'sleep', 'activity', 'recovery'].includes(state.activeView);
    refs.dataQualityNote.textContent = parts.length ? `所选 ${range()} 天内有 ${parts.join('，')}；这些日子在图表中留空，不计入均值。` : '';
  }

  // ---------- overview ----------
  function renderHero() {
    const overview = state.viewModel.overview;
    const score = numberOrNull(overview.recovery_score);
    const tone = score === null ? 'neutral' : score >= 70 ? 'good' : score >= 50 ? 'warning' : 'critical';
    const circumference = 2 * Math.PI * 42;
    const filled = score === null ? 0 : Math.max(0, Math.min(100, score)) / 100 * circumference;
    refs.recoveryRing.dataset.tone = tone;
    refs.recoveryRing.innerHTML = `<svg viewBox="0 0 100 100"><circle class="ring-track" cx="50" cy="50" r="42"/><circle class="ring-fill" cx="50" cy="50" r="42" stroke-dasharray="${filled.toFixed(1)} ${circumference.toFixed(1)}" transform="rotate(-90 50 50)"/></svg><span class="ring-value">${score === null ? '--' : formatNumber(score)}</span><span class="ring-caption">恢复估算</span>`;
    refs.heroDate.textContent = overview.latest_date ? `最近记录 · ${formatDate(overview.latest_date)} ${formatWeekday(overview.latest_date)}` : '暂无记录';
    refs.heroLabel.textContent = overview.recovery_label || (score === null ? '等待数据' : '');
    const rows = allDaily();
    const night = latestValue(rows, 'sleep_hours');
    const nightRow = rows.find(row => row.date === night.date);
    const window = nightRow && sleepWindow(nightRow);
    refs.heroNote.textContent = night.value === null ? '综合 HRV、静息心率与睡眠的本地估算，仅供趋势参考，不是医疗结论。'
      : `最近一晚睡了 ${formatHoursMinutes(night.value)}${window ? `（${formatClock(window[0])} 入睡，${formatClock(window[1])} 起床）` : ''}。恢复估算综合 HRV、静息心率与睡眠，仅供趋势参考。`;
  }
  function renderInsights() {
    const rows = allDaily();
    const items = [];
    const sleep = compareWindows(rows, 'sleep_hours');
    if (sleep.current !== null) {
      const { note, tone } = changeNote('sleep_hours', sleep.current, sleep.previous, '前 7 天');
      items.push([tone || 'neutral', `近 7 天平均每晚睡 ${formatHoursMinutes(sleep.current)}${note ? `，${note}` : ''}。`]);
    }
    for (const key of ['hrv', 'rhr']) {
      const metric = metricWindow(rows, key);
      if (metric.avg7 === null) continue;
      const { note, tone } = changeNote(key, metric.avg7, metric.avg30, ' 30 天均值');
      items.push([tone || 'neutral', `${METRIC_LABELS[key]}${key === 'hrv' ? ' ' : ''}近 7 天均值 ${fmt(key, metric.avg7)}${note ? `，${note}` : ''}。`]);
    }
    const week = selectDateWindow(rows, 7);
    const worn = week.filter(row => numberOrNull(row.steps) !== null);
    if (worn.length) {
      const hits = worn.filter(row => row.steps >= STEP_GOAL).length;
      items.push([hits >= 4 ? 'good' : hits >= 2 ? 'neutral' : 'bad', `近 7 天日均 ${formatNumber(summarize(worn, 'steps').avg)} 步，${hits} 天达到一万步。`]);
    }
    refs.insightList.innerHTML = items.length
      ? items.map(([tone, text]) => `<li class="tone-${tone}"><b aria-hidden="true">${TONE_ICON[tone]}</b><span>${escapeHtml(text)}</span></li>`).join('')
      : '<li class="tone-neutral"><b aria-hidden="true">●</b><span>同步更多天数后，这里会给出近期变化。</span></li>';
  }
  function renderKpis() {
    const rows = series();
    refs.statsGrid.innerHTML = KPI_KEYS.map(([key, view]) => {
      const latest = latestValue(allDaily(), key);
      const stats = summarize(rows, key);
      const [value, unitText] = splitValue(key, latest.value);
      const { note, tone } = changeNote(key, latest.value, stats.avg, '均值');
      return `<button class="metric-card" type="button" data-jump-view="${view}">
        <span class="metric-label"><i class="dot" data-series="${SERIES[key]}"></i>${escapeHtml(METRIC_LABELS[key])}</span>
        <strong class="metric-value">${escapeHtml(value)}${unitText ? `<small>${escapeHtml(unitText)}</small>` : ''}</strong>
        <span class="kpi-note${tone ? ` tone-${tone}` : ''}">${tone ? `<b aria-hidden="true">${TONE_ICON[tone]}</b>` : ''}${escapeHtml(note || '暂无对比')}</span>
        ${sparklineSvg(rows.map(row => row[key]), seriesColor(SERIES[key]))}
        <span class="metric-meta">${latest.date ? `${formatDate(latest.date, 'short')} · ` : ''}${range()} 天均值 ${escapeHtml(fmt(key, stats.avg))}</span>
      </button>`;
    }).join('');
  }
  function barChart(canvasId, rows, key, { lines = [], label = METRIC_LABELS[key] } = {}) {
    const color = seriesColor(SERIES[key]);
    const stats = summarize(rows, key);
    upsertChart(canvasId, {
      type: 'bar',
      data: { labels: shortLabels(rows), datasets: [{ label, data: rows.map(row => row[key] ?? null), backgroundColor: color, borderRadius: 4, maxBarThickness: 22, borderSkipped: 'bottom' }] },
      options: {
        scales: { y: { ...scaleFor(key) } },
        plugins: { tooltip: tooltip(rows, value => fmt(key, value)), reference: { lines: [avgLine(stats.avg, value => formatNumber(value, metricValueDigits(key))), ...lines] } },
      },
    });
  }
  function dotsAndTrend(canvasId, rows, key, { band } = {}) {
    const color = seriesColor(SERIES[key]);
    const rolling = rollingAverage(rows, key);
    upsertChart(canvasId, {
      type: 'line',
      data: {
        labels: shortLabels(rows),
        datasets: [
          { label: '每日', data: rows.map(row => row[key] ?? null), showLine: false, pointRadius: 3, pointHoverRadius: 5, pointBackgroundColor: withAlpha(color, 0.45), pointBorderColor: 'transparent' },
          { label: '7 天均值', data: rolling, borderColor: color, backgroundColor: color, borderWidth: 2, pointRadius: 0, pointHoverRadius: 4, tension: 0.3, spanGaps: true },
        ],
      },
      options: {
        scales: { y: { ...scaleFor(key) } },
        plugins: { tooltip: tooltip(rows, value => fmt(key, value)), reference: band ? { band } : {} },
      },
    });
  }
  function renderOverviewCharts() {
    const rows = series();
    barChart('overviewSleepChart', rows, 'sleep_hours');
    barChart('overviewStepsChart', rows, 'steps', { lines: [{ value: STEP_GOAL, color: token('--text-secondary'), label: '10,000', align: 'left' }] });
    dotsAndTrend('overviewHrvChart', rows, 'hrv');
    dotsAndTrend('overviewRhrChart', rows, 'rhr');
  }
  function renderWeekCompare() {
    const rows = allDaily();
    const keys = ['sleep_hours', 'sleep_score', 'steps', 'active_minutes', 'hrv', 'rhr'];
    const body = keys.map(key => {
      const result = compareWindows(rows, key);
      const change = judgeChange(key, result.current, result.previous);
      const delta = change.delta === null ? '--' : `${TONE_ICON[change.direction === 'flat' ? 'neutral' : change.tone] || ''} ${formatSignedDelta(change.delta, metricValueDigits(key))}`;
      return `<tr><th scope="row"><i class="dot" data-series="${SERIES[key]}"></i>${escapeHtml(METRIC_LABELS[key])}</th><td class="num">${escapeHtml(fmt(key, result.current))}</td><td class="num">${escapeHtml(fmt(key, result.previous))}</td><td class="num tone-${change.direction === 'flat' ? 'neutral' : change.tone}">${escapeHtml(delta)}</td></tr>`;
    }).join('');
    refs.weekCompare.innerHTML = `<table class="compare-table"><thead><tr><th scope="col">指标</th><th scope="col" class="num">近 7 天</th><th scope="col" class="num">前 7 天</th><th scope="col" class="num">变化</th></tr></thead><tbody>${body}</tbody></table>`;
  }
  function renderMonthly() {
    const key = MONTHLY_KEYS.includes(state.monthlyMetric) ? state.monthlyMetric : 'steps';
    refs.monthlyMetricPicker.innerHTML = MONTHLY_KEYS.map(item => `<button type="button" data-metric="${item}" aria-pressed="${item === key}">${escapeHtml(METRIC_LABELS[item])}</button>`).join('');
    refs.monthlyMetricPicker.onclick = event => {
      const metric = event.target.closest('button[data-metric]')?.dataset.metric;
      if (!metric || metric === state.monthlyMetric) return;
      state.monthlyMetric = metric;
      renderMonthly();
    };
    const months = (state.dashboard.charts?.monthly || []).filter(row => /^\d{4}-\d{2}$/.test(row?.period || ''));
    const color = seriesColor(SERIES[key]);
    upsertChart('monthlyChart', {
      type: 'bar',
      data: { labels: months.map(row => `${Number(row.period.slice(5))}月`), datasets: [{ label: METRIC_LABELS[key], data: months.map(row => row[key] ?? null), backgroundColor: color, borderRadius: 4, maxBarThickness: 36 }] },
      options: {
        scales: { y: { ...scaleFor(key) } },
        plugins: { tooltip: { callbacks: { title: items => months[items[0]?.dataIndex]?.period.replace('-', ' 年 ') + ' 月', label: context => ` 日均 ${fmt(key, context.raw)} · ${months[context.dataIndex]?.days ?? '--'} 天记录` } } },
      },
    });
  }
  function renderCorrelations() {
    const correlations = Array.isArray(state.dashboard.correlations) ? state.dashboard.correlations : [];
    refs.correlationCards.innerHTML = correlations.length ? correlations.map(item => {
      const value = numberOrNull(item.coefficient);
      const width = value === null ? 0 : Math.min(50, Math.abs(value) * 50);
      return `<div class="correlation-row"><span>${escapeHtml(item.label || '--')}</span>
        <span class="correlation-bar" aria-hidden="true"><i class="${value !== null && value < 0 ? 'negative' : 'positive'}" data-width="${Math.round(width)}"></i></span>
        <strong class="num">${value === null ? '--' : formatSignedDelta(value, 2)}</strong><small>${escapeHtml(item.strength || '数据不足')} · ${escapeHtml(String(item.points || 0))} 天</small></div>`;
    }).join('') : '<div class="empty-state empty-compact">数据不足，暂无可分析的相关性。</div>';
    // Width via CSSOM keeps markup free of inline style attributes.
    refs.correlationCards.querySelectorAll('[data-width]').forEach(bar => { bar.style.width = `${bar.dataset.width}%`; });
  }

  // ---------- sleep ----------
  function renderSleepView() {
    const rows = series();
    const windows = rows.map(sleepWindow);
    const starts = summarize(windows.filter(Boolean).map(window => ({ v: window[0] })), 'v');
    const ends = summarize(windows.filter(Boolean).map(window => ({ v: window[1] })), 'v');
    const hours = summarize(rows, 'sleep_hours');
    const nights = rows.filter(row => numberOrNull(row.sleep_hours) !== null);
    const enough = nights.filter(row => row.sleep_hours >= SLEEP_TARGET).length;
    const naps = rows.reduce((sum, row) => sum + (numberOrNull(row.nap_count) || 0), 0);
    refs.sleepSummary.innerHTML = [
      kpiCard({ label: '平均睡眠', value: formatHoursMinutes(hours.avg), series: 'sleep', sub: hours.count ? `最短 ${formatNumber(hours.min, 1)} · 最长 ${formatNumber(hours.max, 1)} 小时` : '暂无记录' }),
      kpiCard({ label: '平均作息', value: `${formatClock(starts.avg)} – ${formatClock(ends.avg)}`, sub: starts.sd === null ? '入睡 – 起床' : `入睡时间波动 ±${formatNumber(starts.sd * 60)} 分钟` }),
      kpiCard({ label: '睡满 7 小时', value: `${enough}`, unitText: `/ ${nights.length} 晚`, note: nights.length ? `${formatNumber(enough / nights.length * 100)}% 的夜晚` : '', tone: nights.length ? (enough / nights.length >= 0.6 ? 'good' : 'bad') : undefined }),
      kpiCard({ label: '睡眠效率', value: formatNumber(summarize(rows, 'efficiency').avg), unitText: '%', sub: `平均在床 ${formatHoursMinutes((summarize(rows, 'time_in_bed').avg ?? NaN) / 60)}` }),
      kpiCard({ label: '深睡 / REM 占比', value: `${formatNumber(summarize(rows, 'deep_pct').avg)}% / ${formatNumber(summarize(rows, 'rem_pct').avg)}%`, sub: '常见参考：深睡 13–23%，REM 20–25%' }),
      kpiCard({ label: '小睡', value: `${naps}`, unitText: '次', sub: `所选 ${range()} 天内，不计入夜间时长` }),
    ].join('');

    const hoursOf = key => rows.map(row => numberOrNull(row[key]) === null ? null : Number((row[key] / 60).toFixed(2)));
    const stages = [['深睡', 'minutes_deep', 'stage-deep'], ['REM', 'minutes_rem', 'stage-rem'], ['浅睡', 'minutes_light', 'stage-light'], ['清醒', 'minutes_wake_stages', 'stage-awake']];
    upsertChart('sleepStageChart', {
      type: 'bar',
      data: { labels: shortLabels(rows), datasets: stages.map(([label, key, name]) => ({ label, data: hoursOf(key), backgroundColor: seriesColor(name), borderColor: token('--surface'), borderWidth: { top: 1 }, borderSkipped: 'bottom', maxBarThickness: 26 })) },
      options: {
        scales: { x: { stacked: true }, y: { stacked: true, beginAtZero: true, ticks: { callback: value => `${value}h` } } },
        plugins: {
          tooltip: { callbacks: { title: titleFor(rows), label: context => ` ${context.dataset.label}  ${formatMinutes(rows[context.dataIndex][stages[context.datasetIndex][1]])}`, footer: items => { const row = rows[items[0]?.dataIndex]; return row?.minutes_asleep != null ? `总睡眠 ${formatHoursMinutes(row.minutes_asleep / 60)}` : ''; } } },
          reference: { lines: [{ value: SLEEP_TARGET, color: token('--text-secondary'), label: '7 小时' }] },
        },
      },
    });
    const valid = windows.filter(Boolean);
    // Bounds snap to the tick step so the axis starts and ends on a labelled hour.
    const earliest = valid.length ? Math.min(...valid.map(window => window[0])) - 0.5 : 9;
    const latest = valid.length ? Math.max(...valid.map(window => window[1])) + 0.5 : 21;
    const step = latest - earliest > 12 ? 3 : 2;
    const low = Math.floor(earliest / step) * step;
    const high = Math.ceil(latest / step) * step;
    upsertChart('sleepScheduleChart', {
      type: 'bar',
      data: { labels: shortLabels(rows), datasets: [{ label: '睡眠时段', data: windows, backgroundColor: seriesColor('sleep'), borderRadius: 4, borderSkipped: false, maxBarThickness: 14 }] },
      options: {
        scales: { y: { reverse: true, min: low, max: high, ticks: { stepSize: step, maxTicksLimit: 10, callback: value => formatClock(value) } } },
        plugins: {
          tooltip: { callbacks: { title: titleFor(rows), label: context => Array.isArray(context.raw) ? ` ${formatClock(context.raw[0])} 入睡 – ${formatClock(context.raw[1])} 起床` : '' } },
          reference: { lines: [{ value: starts.avg, dash: true, color: token('--text-muted'), label: starts.avg == null ? '' : `平均入睡 ${formatClock(starts.avg)}` }, { value: ends.avg, dash: true, color: token('--text-muted'), label: ends.avg == null ? '' : `平均起床 ${formatClock(ends.avg)}` }] },
        },
      },
    });
    dotsAndTrend('sleepTrendChart', rows, 'sleep_score');

    renderTable(refs.sleepTableWrap, [
      { key: 'date', label: '日期' }, { key: 'score', label: '估算得分', numeric: true }, { key: 'hours', label: '时长', numeric: true },
      { key: 'deep', label: '深睡', numeric: true }, { key: 'rem', label: 'REM', numeric: true }, { key: 'light', label: '浅睡', numeric: true }, { key: 'awake', label: '清醒', numeric: true },
    ], null, {
      date: value => `${value} ${formatWeekday(value)}`,
      score: value => formatNumber(value, 1), hours: value => formatHoursMinutes(value),
      deep: formatMinutes, rem: formatMinutes, light: formatMinutes, awake: formatMinutes,
    }, { empty: '暂无睡眠记录。' });
  }

  // ---------- activity ----------
  function renderActivityView() {
    const rows = series();
    const worn = rows.filter(row => numberOrNull(row.steps) !== null);
    const steps = summarize(rows, 'steps');
    const best = worn.reduce((top, row) => (top && top.steps >= row.steps ? top : row), null);
    const hits = worn.filter(row => row.steps >= STEP_GOAL).length;
    const very = summarize(rows, 'very_active_minutes');
    refs.activitySummary.innerHTML = [
      kpiCard({ label: '日均步数', value: formatNumber(steps.avg), unitText: '步', series: 'steps', sub: best ? `最高 ${formatNumber(best.steps)} 步（${formatDate(best.date, 'short')}）` : '暂无记录' }),
      kpiCard({ label: '达到一万步', value: `${hits}`, unitText: `/ ${worn.length} 天`, note: worn.length ? `${formatNumber(hits / worn.length * 100)}% 的佩戴日` : '', tone: worn.length ? (hits / worn.length >= 0.5 ? 'good' : 'bad') : undefined }),
      kpiCard({ label: '日均活跃分钟', value: formatNumber(summarize(rows, 'active_minutes').avg), unitText: '分钟', series: 'steps', sub: very.avg === null ? '' : `其中高强度 ${formatNumber(very.avg)} 分钟` }),
      kpiCard({ label: '日均燃脂区分钟', value: formatNumber(summarize(rows, 'active_zone_minutes').avg), unitText: '分钟', series: 'zone', sub: `合计 ${formatNumber(rows.reduce((sum, row) => sum + (numberOrNull(row.active_zone_minutes) || 0), 0))} 分钟` }),
      kpiCard({ label: '日均消耗', value: formatNumber(summarize(rows, 'calories_out').avg), unitText: 'kcal', series: 'energy', sub: '含基础代谢' }),
      kpiCard({ label: '佩戴天数', value: `${worn.length}`, unitText: `/ ${rows.length} 天`, note: worn.length < rows.length ? `${rows.length - worn.length} 天无活动数据` : '全部有数据', tone: worn.length < rows.length ? 'neutral' : 'good' }),
    ].join('');

    barChart('activityTrendChart', rows, 'steps', { lines: [{ value: STEP_GOAL, color: token('--text-secondary'), label: '10,000', align: 'left' }] });
    const intensity = [['高强度', 'very_active_minutes', 'ramp-3'], ['中等强度', 'fairly_active_minutes', 'ramp-2'], ['轻度', 'lightly_active_minutes', 'ramp-1']];
    upsertChart('activityIntensityChart', {
      type: 'bar',
      data: { labels: shortLabels(rows), datasets: intensity.map(([label, key, name]) => ({ label, data: rows.map(row => row[key] ?? null), backgroundColor: seriesColor(name), borderColor: token('--surface'), borderWidth: { top: 1 }, borderSkipped: 'bottom', maxBarThickness: 22 })) },
      options: { scales: { x: { stacked: true }, y: { stacked: true, beginAtZero: true } }, plugins: { tooltip: tooltip(rows, value => formatMinutes(value)) } },
    });
    const weekday = weekdayProfile(rows, 'steps');
    upsertChart('activityWeekdayChart', {
      type: 'bar',
      data: { labels: WEEKDAYS, datasets: [{ label: '日均步数', data: weekday.map(value => value === null ? null : Math.round(value)), backgroundColor: seriesColor('steps'), borderRadius: 4, maxBarThickness: 40 }] },
      options: { scales: { y: { beginAtZero: true } }, plugins: { tooltip: { callbacks: { label: context => ` 日均 ${formatNumber(context.raw)} 步` } }, reference: { lines: [avgLine(steps.avg, value => formatNumber(value))] } } },
    });
    barChart('activityZoneChart', rows, 'active_zone_minutes');
    const burn = summarize(rows, 'calories_out');
    upsertChart('activityBurnChart', {
      type: 'line',
      data: { labels: shortLabels(rows), datasets: [{ label: '消耗热量', data: rows.map(row => row.calories_out ?? null), borderColor: seriesColor('energy'), backgroundColor: seriesColor('energy'), borderWidth: 2, pointRadius: 2, pointHoverRadius: 5, tension: 0 }] },
      options: { scales: { y: { grace: '10%' } }, plugins: { tooltip: tooltip(rows, value => fmt('calories_out', value)), reference: { lines: [avgLine(burn.avg, value => formatNumber(value))] } } },
    });
    renderCalendar();
    renderRecords();

    renderTable(refs.activityLogTableWrap, [
      { key: 'date', label: '日期' }, { key: 'name', label: '运动' }, { key: 'duration', label: '时长', numeric: true },
      { key: 'calories', label: '热量', numeric: true }, { key: 'distance', label: '距离', numeric: true },
    ], null, {
      date: value => value ? `${formatDate(value, 'short')} ${formatWeekday(value)}` : '--',
      name: value => ACTIVITY_NAMES[value] || value,
      duration: formatMinutes,
      calories: value => value == null ? '--' : `${formatNumber(value)} kcal`,
      distance: value => value == null ? '--' : `${formatNumber(value, 1)} km`,
    }, { empty: '暂无运动记录。' });
    renderTable(refs.activityTableWrap, [
      { key: 'date', label: '日期' }, { key: 'steps', label: '步数', numeric: true }, { key: 'active_minutes', label: '活跃分钟', numeric: true },
      { key: 'active_zone_minutes', label: '燃脂区分钟', numeric: true }, { key: 'calories_out', label: '热量', numeric: true }, { key: 'exercise_examples', label: '运动' },
    ], null, {
      date: value => `${value} ${formatWeekday(value)}`,
      steps: value => formatNumber(value), active_minutes: formatMinutes, active_zone_minutes: formatMinutes,
      calories_out: value => value == null ? '--' : `${formatNumber(value)} kcal`,
      exercise_examples: value => value ? String(value).split(/,\s*/).map(name => ACTIVITY_NAMES[name] || name).join('、') : '',
    }, { empty: '暂无活动记录。' });
  }
  function renderCalendar() {
    const rows = fillCalendar(allDaily(), 91);
    if (!rows.length) { refs.stepsCalendar.innerHTML = '<div class="empty-state empty-compact">暂无步数记录。</div>'; return; }
    const lead = (new Date(`${rows[0].date}T00:00:00Z`).getUTCDay() + 6) % 7;
    const level = row => {
      if (row.non_wear) return 'off';
      const steps = numberOrNull(row.steps);
      if (steps === null) return 'none';
      return steps >= 12500 ? 5 : steps >= STEP_GOAL ? 4 : steps >= 7500 ? 3 : steps >= 5000 ? 2 : 1;
    };
    const cells = [...Array.from({ length: lead }, () => '<span class="cal-cell cal-pad"></span>'), ...rows.map(row => {
      const text = `${formatDate(row.date)} ${formatWeekday(row.date)} · ${row.non_wear ? '未佩戴' : numberOrNull(row.steps) === null ? '无记录' : `${formatNumber(row.steps)} 步`}`;
      return `<span class="cal-cell" data-level="${level(row)}" title="${escapeHtml(text)}"></span>`;
    })];
    const columns = Math.ceil(cells.length / 7);
    // Label the first column of each month; the partial first column only when it has room.
    const months = Array.from({ length: columns }, (_, column) => {
      const row = rows[Math.max(0, column * 7 - lead)];
      const day = Number(row.date.slice(8));
      return (column === 0 ? day <= 17 : day <= 7) ? `${Number(row.date.slice(5, 7))}月` : '';
    }).map(label => `<span>${label}</span>`);
    refs.stepsCalendar.innerHTML = `<div class="cal-scroll"><div class="cal-months" data-columns="${columns}">${months.join('')}</div>
      <div class="cal-body"><div class="cal-days" aria-hidden="true"><span>一</span><span></span><span>三</span><span></span><span>五</span><span></span><span>日</span></div>
      <div class="cal-grid" role="img" aria-label="最近 91 天每日步数日历">${cells.join('')}</div></div></div>
      <div class="cal-legend" aria-hidden="true"><span>少</span>${[1, 2, 3, 4, 5].map(item => `<span class="cal-cell" data-level="${item}"></span>`).join('')}<span>多</span><span class="cal-cell" data-level="off"></span><span>未佩戴</span><span class="cal-cell" data-level="none"></span><span>无记录</span></div>`;
    refs.stepsCalendar.querySelectorAll('[data-columns]').forEach(element => { element.style.gridTemplateColumns = `repeat(${element.dataset.columns}, var(--cal-size))`; });
  }
  function renderRecords() {
    const section = state.dashboard.sections?.activity || {};
    const metric = label => (section.metrics || []).find(item => item.label === label);
    const lifetime = section.lifetime || {};
    const best = lifetime.best || {};
    const today = metric('今日距离');
    const record = (label, value, unitText, sub = '') => numberOrNull(value) === null ? '' : kpiCard({ label, value: formatNumber(value, Number.isInteger(value) ? 0 : 1), unitText, sub });
    const all = allDaily().filter(row => numberOrNull(row.steps) !== null);
    const top = all.reduce((max, row) => (max && max.steps >= row.steps ? max : row), null);
    const html = [
      record('今日距离', today?.value, 'km'),
      top ? kpiCard({ label: '近 90 天单日最高', value: formatNumber(top.steps), unitText: '步', sub: formatDate(top.date) }) : '',
      record('累计步数', lifetime.steps, '步'),
      record('累计距离', lifetime.distance, 'km'),
      record('累计爬楼', lifetime.floors, '层'),
      record('历史最佳单日步数', best.steps?.value, '步', best.steps?.date ? formatDate(best.steps.date) : ''),
      record('历史最佳单日距离', best.distance?.value, 'km', best.distance?.date ? formatDate(best.distance.date) : ''),
      record('历史最佳单日爬楼', best.floors?.value, '层', best.floors?.date ? formatDate(best.floors.date) : ''),
    ].filter(Boolean);
    refs.activityRecords.innerHTML = html.length ? html.join('') : '<div class="empty-state empty-compact">暂无累计数据。</div>';
    refs.lifetimeNote.textContent = lifetime.cached_at ? `累计与历史最佳来自 Fitbit 历史缓存（${formatDate(lifetime.cached_at)}），Google Health 不再提供该数据。` : '今日摘要、近期最高与历史最佳纪录。';
  }

  // ---------- recovery ----------
  function baselineBand(key) {
    const stats = summarize(selectDateWindow(allDaily(), 30), key);
    return stats.avg === null || stats.sd === null ? null : { from: stats.avg - stats.sd, to: stats.avg + stats.sd, color: withAlpha(seriesColor(SERIES[key]), 0.12) };
  }
  function renderRecoveryView() {
    const rows = series();
    const overview = state.viewModel.overview;
    const score = numberOrNull(overview.recovery_score);
    const cards = [kpiCard({ label: '恢复估算', value: score === null ? '--' : formatNumber(score), unitText: '/ 100', note: overview.recovery_label || '', tone: score === null ? undefined : score >= 70 ? 'good' : score >= 50 ? 'warning' : 'critical', sub: '本地估算，非医疗结论' })];
    for (const [key, label] of [['hrv', 'HRV · 7 天均值'], ['rhr', '静息心率 · 7 天均值'], ['sleep_score', '睡眠得分 · 7 天均值']]) {
      const metric = metricWindow(allDaily(), key);
      const [value, unitText] = splitValue(key, metric.avg7);
      const { note, tone } = changeNote(key, metric.avg7, metric.avg30, ' 30 天均值');
      cards.push(kpiCard({ label, value, unitText, series: SERIES[key], note, tone, sub: `30 天均值 ${fmt(key, metric.avg30)} · ${metric.points} 天有效` }));
    }
    refs.recoverySummary.innerHTML = cards.join('');
    refs.recoveryEstimate.textContent = 'Fitflare 恢复估算根据 HRV、静息心率与睡眠相对个人基线计算，仅供趋势参考，不代表 Google Health 官方评分或医疗结论。';
    dotsAndTrend('recoveryTrendChart', rows, 'hrv', { band: baselineBand('hrv') });
    dotsAndTrend('recoveryRhrChart', rows, 'rhr', { band: baselineBand('rhr') });
    const points = rows.filter(row => numberOrNull(row.sleep_hours) !== null && numberOrNull(row.hrv) !== null);
    upsertChart('recoveryScatterChart', {
      type: 'scatter',
      data: { datasets: [{ label: '每晚', data: points.map(row => ({ x: row.sleep_hours, y: row.hrv })), backgroundColor: withAlpha(seriesColor('hrv'), 0.6), borderColor: token('--surface'), borderWidth: 1, pointRadius: 5, pointHoverRadius: 7 }] },
      options: {
        scales: { x: { title: { display: true, text: '睡眠时长（小时）' }, grace: '5%' }, y: { title: { display: true, text: 'HRV (ms)' }, grace: '10%' } },
        plugins: { tooltip: { callbacks: { title: items => { const row = points[items[0]?.dataIndex]; return row ? `${formatDate(row.date)} ${formatWeekday(row.date)}` : ''; }, label: context => ` 睡眠 ${formatHoursMinutes(context.raw.x)} · HRV ${formatNumber(context.raw.y, 1)} ms` } } },
      },
    });
    renderTable(refs.recoveryTableWrap, [
      { key: 'date', label: '日期' }, { key: 'hrv', label: 'HRV', numeric: true }, { key: 'deep_rmssd', label: '深睡 HRV', numeric: true }, { key: 'rhr', label: '静息心率', numeric: true },
    ], null, {
      date: value => `${value} ${formatWeekday(value)}`,
      hrv: value => value == null ? '--' : `${formatNumber(value, 1)} ms`,
      deep_rmssd: value => value == null ? '--' : `${formatNumber(value, 1)} ms`,
      rhr: value => value == null ? '--' : `${formatNumber(value)} bpm`,
    }, { empty: '暂无恢复记录。' });
  }

  // ---------- body & nutrition ----------
  function bmiCategory(value) {
    if (numberOrNull(value) === null) return '';
    return value < 18.5 ? ['偏瘦', 'warning'] : value < 24 ? ['正常范围', 'good'] : value < 28 ? ['超重', 'warning'] : ['肥胖', 'critical'];
  }
  function renderBodyView() {
    const sections = state.dashboard.sections || {};
    const metrics = [...(sections.body?.metrics || []), ...(sections.vitals?.metrics || [])];
    const missing = [];
    const cards = metrics.map(item => {
      if (numberOrNull(item.value) === null) { missing.push(item.label); return ''; }
      const digits = Number.isInteger(item.value) ? 0 : 1;
      if (item.label === 'BMI') {
        const [note, tone] = bmiCategory(item.value);
        return kpiCard({ label: 'BMI', value: formatNumber(item.value, 1), note, tone, sub: '中国成人标准 18.5–23.9' });
      }
      if (item.label === '当前体重') {
        const height = numberOrNull(state.viewModel.profile.height_cm);
        return kpiCard({ label: '体重', value: formatNumber(item.value, 1), unitText: 'kg', sub: height ? `身高 ${formatNumber(height)} cm` : '' });
      }
      const date = /最近记录：(\d{4}-\d{2}-\d{2})/.exec(item.detail || '')?.[1];
      const sub = item.label === '皮温' ? '相对个人基线的夜间偏差' : date ? `${formatDate(date)} 记录` : '';
      return kpiCard({ label: item.label, value: item.label === '皮温' ? formatSignedDelta(item.value, 1) : formatNumber(item.value, digits), unitText: item.unit || '', sub });
    }).filter(Boolean);
    refs.bodySummary.innerHTML = cards.length ? cards.join('') : '<div class="empty-state empty-compact">暂无体征数据。</div>';
    refs.bodyMissingNote.hidden = !missing.length;
    refs.bodyMissingNote.textContent = missing.length ? `尚无记录：${missing.join('、')}。在 Google Health 中添加后会自动显示。` : '';

    const pretty = text => String(text || '').replace(/lowerBoundPercentage/g, '下限').replace(/upperBoundPercentage/g, '上限').replace(/平均值/g, '均值');
    renderTable(refs.vitalTableWrap, [
      { key: 'date', label: '日期' }, { key: 'metric', label: '指标' }, { key: 'value', label: '数值', numeric: true }, { key: 'detail', label: '说明' },
    ], null, {
      date: value => formatDate(value),
      value: (value, row) => value == null ? '--' : `${row.metric === '皮温' ? formatSignedDelta(value, 1) : formatNumber(value, 1)} ${row.unit || ''}`.trim(),
      detail: pretty,
    }, { empty: '暂无夜间体征记录。' });
    renderTable(refs.bodyTableWrap, [
      { key: 'date', label: '日期' }, { key: 'type', label: '类型' }, { key: 'value', label: '数值', numeric: true }, { key: 'detail', label: '说明' },
    ], null, { date: value => formatDate(value), value: (value, row) => value == null ? '--' : `${formatNumber(value, 1)} ${row.unit || ''}`.trim() }, { empty: '暂无体重或体脂记录。' });

    const lifestyle = (sections.lifestyle?.metrics || []).filter(item => numberOrNull(item.value));
    refs.lifestyleMetricGrid.innerHTML = lifestyle.length
      ? lifestyle.map(item => kpiCard({ label: item.label, value: formatNumber(item.value), unitText: item.unit || '', sub: item.detail || '' })).join('')
      : '<div class="empty-state empty-compact">还没有饮食或饮水记录。在 Google Health 中记录后，下一次同步会显示在这里。</div>';
    refs.foodTableWrap.hidden = !lifestyle.length;
    if (lifestyle.length) {
      renderTable(refs.foodTableWrap, [
        { key: 'name', label: '食物' }, { key: 'brand', label: '品牌' }, { key: 'calories', label: '热量', numeric: true }, { key: 'amount', label: '份量' }, { key: 'last_eaten', label: '最近食用' },
      ], null, { calories: value => value == null ? '--' : `${formatNumber(value)} kcal`, last_eaten: value => formatDate(value) }, { empty: '暂无食物记录。' });
    }
  }

  // ---------- devices & data ----------
  function renderAccountView() {
    const status = state.viewModel.snapshotStatus || {};
    const overview = state.viewModel.overview;
    const coverage = state.viewModel.coverage || {};
    const summary = status.fetch_summary || {};
    const granted = status.scopes || [];
    const requested = status.requested_scopes || [];
    const missing = status.missing_scopes || [];
    const provider = status.provider === 'google_health' ? 'Google Health' : status.provider ? 'Fitbit' : '--';
    refs.accountSummary.innerHTML = [
      kpiCard({ label: '数据来源', value: provider, note: status.sync_status === 'complete' ? '最近一次同步完成' : status.sync_status ? `同步状态：${status.sync_status}` : '', tone: status.sync_status === 'complete' ? 'good' : status.sync_status ? 'warning' : undefined, sub: status.saved_at ? `${formatRelativeTime(status.saved_at)}更新` : '' }),
      kpiCard({ label: '历史天数', value: formatNumber(overview.tracked_days), unitText: '天', sub: coverage.daily?.start_date ? `自 ${formatDate(coverage.daily.start_date)}` : '' }),
      kpiCard({ label: '接口同步', value: `${summary.ok ?? '--'}`, unitText: `/ ${summary.total ?? '--'}`, note: summary.failed ? `${summary.failed} 个失败` : '无失败', tone: summary.failed ? 'critical' : summary.total ? 'good' : undefined, sub: summary.skipped ? `${summary.skipped} 个新服务不提供，保留历史缓存` : '' }),
      kpiCard({ label: '授权范围', value: `${granted.length}`, unitText: `/ ${requested.length || granted.length}`, note: missing.length ? `缺少 ${missing.length} 项` : '已全部授权', tone: missing.length ? 'warning' : granted.length ? 'good' : undefined }),
    ].join('');

    renderTable(refs.deviceList, [{ key: 'device', label: '设备' }], null, {}, {
      empty: '暂无设备信息。',
      render: rows => rows.map(row => {
        const [batteryText, batteryTone] = BATTERY[row.battery] || [row.battery ? `电量 ${row.battery}` : '电量未知', 'neutral'];
        const stale = row.last_sync && Date.now() - new Date(row.last_sync).getTime() > 7 * DAY;
        return `<div class="device-row"><div><strong>${escapeHtml(row.device || '未知设备')}</strong><small>${escapeHtml(row.type === 'TRACKER' ? '手环 / 手表' : row.type || '')}</small></div>
          <div class="device-meta"><span class="chip tone-${batteryTone}">${escapeHtml(batteryText)}</span>
          <small class="${stale ? 'tone-warning' : ''}" title="${escapeHtml(formatDateTime(row.last_sync))}">${stale ? '已长时间未同步 · ' : ''}${escapeHtml(formatRelativeTime(row.last_sync))}同步</small></div></div>`;
      }).join(''),
    });

    const domains = [['sleep', '睡眠'], ['activity', '活动'], ['hrv', 'HRV'], ['rhr', '静息心率']];
    const spanDays = item => item?.start_date && item?.end_date ? Math.round((Date.parse(item.end_date) - Date.parse(item.start_date)) / DAY) + 1 : 0;
    refs.coverageList.innerHTML = domains.map(([key, label]) => {
      const item = coverage[key];
      if (!item?.count) return `<div class="coverage-row"><span>${label}</span><span class="coverage-bar" aria-hidden="true"><i data-width="0"></i></span><small>暂无</small></div>`;
      const ratio = Math.min(100, Math.round(item.count / Math.max(1, spanDays(item)) * 100));
      return `<div class="coverage-row"><span>${label}</span><span class="coverage-bar" aria-hidden="true"><i data-width="${ratio}"></i></span><small>${formatNumber(item.count)} 天 · ${ratio}% · ${formatDate(item.start_date, 'short')}–${formatDate(item.end_date, 'short')}</small></div>`;
    }).join('');
    refs.coverageList.querySelectorAll('[data-width]').forEach(bar => { bar.style.width = `${bar.dataset.width}%`; });

    const scopeName = scope => SCOPE_NAMES[String(scope).split('googlehealth.').pop()] || String(scope).split('/').pop();
    refs.scopeList.innerHTML = granted.length || missing.length
      ? `<div class="chip-row">${granted.map(scope => `<span class="chip tone-good" title="${escapeHtml(scope)}"><b aria-hidden="true">✓</b>${escapeHtml(scopeName(scope))}</span>`).join('')}${missing.map(scope => `<span class="chip tone-warning" title="${escapeHtml(scope)}"><b aria-hidden="true">!</b>${escapeHtml(scopeName(scope))}（未授权）</span>`).join('')}</div>
         <p class="muted">${missing.length ? '管理员重新授权后，下一次同步会补齐缺少的数据。' : '当前页面需要的读取权限均已授予。'}</p>`
      : '<div class="empty-state empty-compact">暂无授权信息。</div>';

    renderTable(refs.badgeTableWrap, [
      { key: 'name', label: '徽章' }, { key: 'category', label: '类别' }, { key: 'date', label: '获得日期' },
    ], null, { date: value => formatDate(value) }, { empty: '暂无徽章。' });
    renderTable(refs.alarmTableWrap, [
      { key: 'device', label: '设备' }, { key: 'time', label: '时间' }, { key: 'enabled', label: '状态' }, { key: 'recurring', label: '重复' },
    ], null, {}, { empty: '没有设置闹钟。' });
    renderTable(refs.endpointTableWrap, [
      { key: 'dataset', label: '接口' }, { key: 'group', label: '分组' }, { key: 'status', label: '状态' }, { key: 'updated_at', label: '更新时间' },
    ], null, { updated_at: value => formatDateTime(value) }, { empty: '暂无同步记录。' });

    const files = state.viewModel.account.files || {};
    refs.fileList.innerHTML = state.viewModel.account.cacheLayers.map(item => `<div class="file-item"><strong>${escapeHtml(item.label)}</strong><small>${escapeHtml(item.detail)}</small>${files[item.key] ? `<code>${escapeHtml(files[item.key])}</code>` : ''}</div>`).join('');
  }

  // ---------- profiles ----------
  function renderFamily() {
    const cards = state.profileSummaries;
    if (!cards.length) { refs.familyGrid.innerHTML = '<div class="empty-state">当前没有档案可对比。</div>'; return; }
    refs.familyGrid.innerHTML = cards.map(card => {
      const score = numberOrNull(card.recovery_score);
      const tone = score === null ? 'neutral' : score >= 70 ? 'good' : score >= 50 ? 'warning' : 'critical';
      const current = card.id === state.selectedProfile;
      return `<article class="surface family-profile-card${current ? ' is-current' : ''}">
        <div class="family-head"><div><h3>${escapeHtml(card.display_name || card.id || '--')}</h3><small>最近记录 ${escapeHtml(formatDate(card.latest_date))}</small></div>
          <button class="button${current ? ' button-light' : ''}" type="button" data-jump-profile="${escapeHtml(card.id || '')}">${current ? '当前档案' : '查看'}</button></div>
        <div class="family-score"><span class="chip tone-${tone}">恢复估算 ${score === null ? '--' : formatNumber(score)}</span><small>${escapeHtml(card.recovery_label || '等待数据')}</small></div>
        <dl class="family-kpis">
          <div><dt>睡眠得分</dt><dd>${escapeHtml(card.sleep_score == null ? '--' : formatNumber(card.sleep_score, 1))}</dd></div>
          <div><dt>步数</dt><dd>${escapeHtml(card.steps == null ? '--' : formatNumber(card.steps))}</dd></div>
          <div><dt>HRV</dt><dd>${escapeHtml(card.hrv == null ? '--' : `${formatNumber(card.hrv, 1)} ms`)}</dd></div>
          <div><dt>静息心率</dt><dd>${escapeHtml(card.rhr == null ? '--' : `${formatNumber(card.rhr)} bpm`)}</dd></div>
        </dl></article>`;
    }).join('');
  }

  return {
    overview: () => { renderHero(); renderInsights(); renderKpis(); renderOverviewCharts(); renderWeekCompare(); renderMonthly(); renderCorrelations(); },
    sleep: renderSleepView, activity: renderActivityView, recovery: renderRecoveryView,
    body: renderBodyView, account: renderAccountView, family: renderFamily, note: renderDataNote,
  };
}
