import { escapeHtml, formatDate, formatDateTime, formatNumber, formatDetailValue, formatMinutes, minutesToHours, formatMetricValue, formatSignedDelta } from './format.js';
import { weeklySeries, metricWindow } from './data.js';
import { upsertChart, dualAxisOptions, stackedOptions } from './charts.js';

export function createViews({ state, refs, getDailySeries, renderTable }) {
  function renderCorrelations() {
    const correlations = Array.isArray(state.dashboard.correlations) ? state.dashboard.correlations : []
    if (!correlations.length) {
      refs.correlationCards.innerHTML = `<div class="empty-state">没有可分析的数据。</div>`
      return
    }
    refs.correlationCards.innerHTML = correlations
      .map((item) => {
        return `
          <div class="narrative-pill">
            <div class="metric-label">${escapeHtml(item.label || "--")}</div>
            <strong>${escapeHtml(item.coefficient == null ? "--" : item.coefficient)}</strong>
            <div class="metric-meta">
              ${escapeHtml(item.strength || "数据不足")}<br>
              重叠点数：${escapeHtml(String(item.points || 0))}
            </div>
          </div>
        `
      })
      .join("")
  }

  function renderOverviewCharts() {
    const daily = getDailySeries()
    const weekly = weeklySeries(daily)

    upsertChart("overviewTrendChart", {
      type: "bar",
      data: {
        labels: daily.map((row) => formatDate(row.date, "short")),
        datasets: [
          {
            type: "bar",
            label: "步数",
            data: daily.map((row) => row.steps ?? null),
            backgroundColor: "rgba(10, 132, 255, 0.28)",
            borderRadius: 8,
            yAxisID: "y",
          },
          {
            type: "line",
            label: "睡眠得分",
            data: daily.map((row) => row.sleep_score ?? null),
            borderColor: "#FFB020",
            backgroundColor: "rgba(255, 176, 32, 0.18)",
            tension: 0.32,
            borderWidth: 2.5,
            pointRadius: 0,
            yAxisID: "y1",
          },
        ],
      },
      options: dualAxisOptions({
        y: { title: { display: true, text: "步数" } },
        y1: { position: "right", title: { display: true, text: "睡眠得分" }, min: 0, max: 100, grid: { drawOnChartArea: false } },
      }),
    })

    upsertChart("weeklyTrendChart", {
      type: "line",
      data: {
        labels: weekly.map((row) => formatDate(row.period, "short")),
        datasets: [
          {
            label: "周平均睡眠时长",
            data: weekly.map((row) => row.sleep_hours ?? null),
            borderColor: "#FFB020",
            backgroundColor: "rgba(255, 176, 32, 0.18)",
            tension: 0.32,
            pointRadius: 3,
            pointHoverRadius: 5,
            fill: true,
            yAxisID: "y",
          },
          {
            label: "周平均 HRV",
            data: weekly.map((row) => row.hrv ?? null),
            borderColor: "#0A84FF",
            backgroundColor: "rgba(10, 132, 255, 0.16)",
            tension: 0.32,
            pointRadius: 3,
            pointHoverRadius: 5,
            fill: true,
            yAxisID: "y1",
          },
        ],
      },
      options: dualAxisOptions({
        y: { title: { display: true, text: "小时" } },
        y1: { position: "right", title: { display: true, text: "ms" }, grid: { drawOnChartArea: false } },
      }),
    })
  }

  function renderSleepView() {
    const daily = getDailySeries()

    upsertChart("sleepTrendChart", {
      type: "line",
      data: {
        labels: daily.map((row) => formatDate(row.date, "short")),
        datasets: [
          {
            label: "睡眠得分",
            data: daily.map((row) => row.sleep_score ?? null),
            borderColor: "#7C8CFF",
            backgroundColor: "rgba(124, 140, 255, 0.16)",
            tension: 0.32,
            borderWidth: 2.5,
            pointRadius: 0,
            yAxisID: "y",
          },
          {
            label: "睡眠时长",
            data: daily.map((row) => row.sleep_hours ?? null),
            borderColor: "#FFB020",
            backgroundColor: "rgba(255, 176, 32, 0.18)",
            tension: 0.32,
            borderWidth: 2.5,
            pointRadius: 0,
            yAxisID: "y1",
          },
        ],
      },
      options: dualAxisOptions({
        y: { min: 0, max: 100, title: { display: true, text: "睡眠得分" } },
        y1: { position: "right", title: { display: true, text: "小时" }, grid: { drawOnChartArea: false } },
      }),
    })

    upsertChart("sleepStageChart", {
      type: "bar",
      data: {
        labels: daily.map((row) => formatDate(row.date, "short")),
        datasets: [
          {
            label: "深睡",
            data: daily.map((row) => minutesToHours(row.minutes_deep)),
            backgroundColor: "rgba(124, 140, 255, 0.78)",
            borderRadius: 8,
          },
          {
            label: "REM",
            data: daily.map((row) => minutesToHours(row.minutes_rem)),
            backgroundColor: "rgba(48, 213, 200, 0.68)",
            borderRadius: 8,
          },
          {
            label: "浅睡",
            data: daily.map((row) => minutesToHours(row.minutes_light)),
            backgroundColor: "rgba(255, 176, 32, 0.64)",
            borderRadius: 8,
          },
        ],
      },
      options: stackedOptions("小时"),
    })

    renderTable(refs.sleepTableWrap, [
      { key: "date", label: "日期" },
      { key: "score", label: "睡眠得分" },
      { key: "hours", label: "时长" },
      { key: "deep", label: "深睡" },
      { key: "rem", label: "REM" },
      { key: "light", label: "浅睡" },
      { key: "awake", label: "清醒" },
    ], state.dashboard.tables?.sleep || [], {
      score: (value) => formatNumber(value, 1),
      hours: (value) => value == null ? "--" : `${formatNumber(value, 1)} 小时`,
      deep: formatMinutes,
      rem: formatMinutes,
      light: formatMinutes,
      awake: formatMinutes,
    })
  }

  function renderActivityView() {
    const daily = getDailySeries()

    upsertChart("activityTrendChart", {
      type: "bar",
      data: {
        labels: daily.map((row) => formatDate(row.date, "short")),
        datasets: [
          {
            label: "步数",
            data: daily.map((row) => row.steps ?? null),
            backgroundColor: "rgba(52, 199, 89, 0.26)",
            borderRadius: 8,
            yAxisID: "y",
          },
          {
            type: "line",
            label: "活跃分钟",
            data: daily.map((row) => row.active_minutes ?? null),
            borderColor: "#30D5C8",
            backgroundColor: "rgba(48, 213, 200, 0.18)",
            tension: 0.32,
            pointRadius: 0,
            borderWidth: 2.5,
            yAxisID: "y1",
          },
        ],
      },
      options: dualAxisOptions({
        y: { title: { display: true, text: "步数" } },
        y1: { position: "right", title: { display: true, text: "分钟" }, grid: { drawOnChartArea: false } },
      }),
    })

    upsertChart("activityBurnChart", {
      type: "line",
      data: {
        labels: daily.map((row) => formatDate(row.date, "short")),
        datasets: [
          {
            label: "燃脂区分钟",
            data: daily.map((row) => row.active_zone_minutes ?? null),
            borderColor: "#FF6B6B",
            backgroundColor: "rgba(255, 107, 107, 0.16)",
            tension: 0.32,
            pointRadius: 0,
            borderWidth: 2.5,
            yAxisID: "y",
          },
          {
            label: "消耗热量",
            data: daily.map((row) => row.calories_out ?? null),
            borderColor: "#FFB020",
            backgroundColor: "rgba(255, 176, 32, 0.16)",
            tension: 0.32,
            pointRadius: 0,
            borderWidth: 2.5,
            yAxisID: "y1",
          },
        ],
      },
      options: dualAxisOptions({
        y: { title: { display: true, text: "分钟" } },
        y1: { position: "right", title: { display: true, text: "kcal" }, grid: { drawOnChartArea: false } },
      }),
    })

    renderDetailGrid(refs.activityContextGrid, state.dashboard.sections?.activity?.metrics || [])

    renderTable(refs.activityLogTableWrap, [
      { key: "date", label: "日期" },
      { key: "name", label: "活动" },
      { key: "duration", label: "时长" },
      { key: "calories", label: "热量" },
      { key: "distance", label: "距离" },
      { key: "detail", label: "说明" },
    ], state.dashboard.tables?.activity_logs || [], {
      duration: formatMinutes,
      calories: (value) => value == null ? "--" : `${formatNumber(value)} kcal`,
      distance: (value) => value == null ? "--" : `${formatNumber(value, 1)} km`,
    })

    renderTable(refs.activityTableWrap, [
      { key: "date", label: "日期" },
      { key: "steps", label: "步数" },
      { key: "active_minutes", label: "活跃分钟" },
      { key: "active_zone_minutes", label: "燃脂区分钟" },
      { key: "calories_out", label: "热量" },
      { key: "exercise_examples", label: "活动样例" },
    ], state.dashboard.tables?.activity || [], {
      steps: (value) => formatNumber(value),
      active_minutes: formatMinutes,
      active_zone_minutes: formatMinutes,
      calories_out: (value) => value == null ? "--" : `${formatNumber(value)} kcal`,
    })
  }

  function renderRecoveryView() {
    const daily = getDailySeries()
    renderRecoverySummary()

    upsertChart("recoveryTrendChart", {
      type: "line",
      data: {
        labels: daily.map((row) => formatDate(row.date, "short")),
        datasets: [
          {
            label: "HRV",
            data: daily.map((row) => row.hrv ?? null),
            borderColor: "#30D5C8",
            backgroundColor: "rgba(48, 213, 200, 0.16)",
            tension: 0.32,
            borderWidth: 2.5,
            pointRadius: 0,
            yAxisID: "y",
          },
          {
            label: "静息心率",
            data: daily.map((row) => row.rhr ?? null),
            borderColor: "#FF6B6B",
            backgroundColor: "rgba(255, 107, 107, 0.16)",
            tension: 0.32,
            borderWidth: 2.5,
            pointRadius: 0,
            yAxisID: "y1",
          },
        ],
      },
      options: dualAxisOptions({
        y: { title: { display: true, text: "HRV (ms)" } },
        y1: { position: "right", title: { display: true, text: "RHR (bpm)" }, grid: { drawOnChartArea: false } },
      }),
    })

    upsertChart("recoveryScatterChart", {
      type: "scatter",
      data: {
        datasets: [
          {
            label: "睡眠得分 / HRV",
            data: daily
              .filter((row) => row.sleep_score != null && row.hrv != null)
              .map((row) => ({ x: row.sleep_score, y: row.hrv })),
            borderColor: "#30D5C8",
            backgroundColor: "rgba(48, 213, 200, 0.38)",
            pointRadius: 5,
          },
        ],
      },
      options: {
        scales: {
          x: { title: { display: true, text: "睡眠得分" }, min: 0, max: 100 },
          y: { title: { display: true, text: "HRV (ms)" } },
        },
      },
    })

    renderTable(refs.recoveryTableWrap, [
      { key: "date", label: "日期" },
      { key: "hrv", label: "HRV" },
      { key: "deep_rmssd", label: "深睡 HRV" },
      { key: "rhr", label: "静息心率" },
    ], state.dashboard.tables?.recovery || [], {
      hrv: (value) => value == null ? "--" : `${formatNumber(value, 1)} ms`,
      deep_rmssd: (value) => value == null ? "--" : `${formatNumber(value, 1)} ms`,
      rhr: (value) => value == null ? "--" : `${formatNumber(value)} bpm`,
    })
  }

  function renderBodyView() {
    renderDetailGrid(refs.bodyMetricGrid, state.dashboard.sections?.body?.metrics || [])
    renderDetailGrid(refs.vitalMetricGrid, state.dashboard.sections?.vitals?.metrics || [])

    renderTable(refs.bodyTableWrap, [
      { key: "date", label: "日期" },
      { key: "type", label: "类型" },
      { key: "value", label: "值" },
      { key: "detail", label: "说明" },
    ], state.dashboard.tables?.body || [], {
      value: (value, row) => value == null ? "--" : `${formatNumber(value, 1)} ${row.unit || ""}`.trim(),
    })

    renderTable(refs.vitalTableWrap, [
      { key: "date", label: "日期" },
      { key: "metric", label: "指标" },
      { key: "value", label: "值" },
      { key: "detail", label: "摘要" },
    ], state.dashboard.tables?.vitals || [], {
      value: (value, row) => value == null ? "--" : `${formatNumber(value, 1)} ${row.unit || ""}`.trim(),
    })
  }

  function renderLifestyleView() {
    renderDetailGrid(refs.lifestyleMetricGrid, state.dashboard.sections?.lifestyle?.metrics || [])

    renderTable(refs.foodTableWrap, [
      { key: "name", label: "食物" },
      { key: "brand", label: "品牌" },
      { key: "calories", label: "热量" },
      { key: "amount", label: "份量" },
      { key: "last_eaten", label: "最近食用" },
    ], state.dashboard.tables?.foods || [], {
      calories: (value) => value == null ? "--" : `${formatNumber(value)} kcal`,
      last_eaten: (value) => formatDate(value),
    })
  }

  function renderAccountView() {
    const snapshotStatus = state.viewModel.snapshotStatus || {}
    const accountSection = state.dashboard.sections?.account || {}
    const grantedScopes = snapshotStatus.scopes || []
    const missingScopes = snapshotStatus.missing_scopes || []
    const requestedScopes = snapshotStatus.requested_scopes || []
    const fetchSummary = snapshotStatus.fetch_summary || {}

    renderDetailGrid(refs.accountMetricGrid, accountSection.metrics || [])

    renderTable(refs.deviceTableWrap, [
      { key: "device", label: "设备" },
      { key: "type", label: "类型" },
      { key: "battery", label: "电量" },
      { key: "last_sync", label: "最近同步" },
      { key: "status", label: "标识" },
    ], state.dashboard.tables?.devices || [], {
      last_sync: (value) => formatDateTime(value),
    })

    renderTable(refs.badgeTableWrap, [
      { key: "name", label: "徽章" },
      { key: "category", label: "分类" },
      { key: "value", label: "值" },
      { key: "date", label: "日期" },
    ], state.dashboard.tables?.badges || [], {
      date: (value) => formatDate(value),
    })

    renderTable(refs.alarmTableWrap, [
      { key: "device", label: "设备" },
      { key: "time", label: "时间" },
      { key: "enabled", label: "状态" },
      { key: "recurring", label: "重复" },
    ], state.dashboard.tables?.alarms || [])

    renderTable(refs.endpointTableWrap, [
      { key: "dataset", label: "接口" },
      { key: "group", label: "分组" },
      { key: "scope", label: "所需权限" },
      { key: "status", label: "状态" },
      { key: "updated_at", label: "更新时间" },
    ], state.dashboard.tables?.endpoints || [], {
      updated_at: (value) => formatDateTime(value),
    })

    refs.scopeList.innerHTML = `
      <div class="scope-chip">
        <strong>同步概况</strong>
        <small>已同步 ${fetchSummary.ok || 0}/${fetchSummary.total || 0} 类数据，缺少 ${missingScopes.length} 项权限。</small>
        <div class="scope-row">
          ${(grantedScopes.length ? grantedScopes : ["暂无已授权权限"]).map((scope) => `<span class="tag">${escapeHtml(scope)}</span>`).join("")}
        </div>
      </div>
      <div class="scope-chip">
        <strong>待补充权限</strong>
        <small>重新授权后，下次同步会补充对应的数据。</small>
        <div class="scope-row">
          ${(missingScopes.length ? missingScopes : ["已获得所需权限"]).map((scope) => `<span class="tag scope-missing">${escapeHtml(scope)}</span>`).join("")}
        </div>
      </div>
      <div class="scope-chip">
        <strong>申请的权限</strong>
        <small>用于读取你的体征、生活与账户记录。</small>
        <div class="scope-row">
          ${(requestedScopes.length ? requestedScopes : ["暂无配置"]).map((scope) => `<span class="tag">${escapeHtml(scope)}</span>`).join("")}
        </div>
      </div>
    `
  }

  function renderDetailGrid(container, metrics) {
    if (!container) return
    if (!metrics.length) {
      container.innerHTML = `<div class="empty-state">当前没有可显示的数据。</div>`
      return
    }
    container.innerHTML = metrics
      .map((item) => {
        const unit = item.unit ? `<span>${escapeHtml(item.unit)}</span>` : ""
        const hint = item.hint ? `<div class="detail-hint">${escapeHtml(item.hint)}</div>` : ""
        return `
          <article class="detail-card" data-tone="${escapeHtml(item.tone || "blue")}">
            <div class="detail-label">${escapeHtml(item.label || "--")}</div>
            <div class="detail-value">${escapeHtml(formatDetailValue(item.value))}${unit}</div>
            <div class="detail-meta">${escapeHtml(item.detail || "暂无说明")}</div>
            ${hint}
          </article>
        `
      })
      .join("")
  }

  function renderRecoverySummary() {
    const rows = state.dashboard.charts?.daily || [];
    const metrics = [['hrv', 'HRV', 'ms'], ['rhr', '静息心率', 'bpm'], ['sleep_score', '睡眠得分', '分']];
    refs.recoverySummary.innerHTML = metrics.map(([key, label, unit]) => {
      const metric = metricWindow(rows, key);
      return `<article class="detail-card"><div class="detail-label">${label} · 7 天均值</div>
        <div class="detail-value">${formatMetricValue(key, metric.avg7, unit)}</div>
        <div class="detail-meta">30 天均值 ${formatMetricValue(key, metric.avg30, unit)}<br>
        差值 ${formatSignedDelta(metric.delta, key === 'rhr' ? 0 : 1)} ${unit} · 有效记录 ${metric.points} 天</div></article>`;
    }).join('');
    refs.recoveryEstimate.textContent = `本地恢复估算：${formatNumber(state.viewModel.overview.recovery_score)}。仅供趋势参考，不是 Fitbit 官方指标或医疗结论。`;
  }
return { overview: () => { renderOverviewCharts(); renderCorrelations(); }, sleep: renderSleepView, activity: renderActivityView, recovery: renderRecoveryView, body: renderBodyView, lifestyle: renderLifestyleView, account: renderAccountView };
}
