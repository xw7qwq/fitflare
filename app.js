const state = {
  profiles: [],
  selectedProfile: null,
  dashboard: null,
  profileSummaries: [],
  activeView: "overview",
  authProfile: null,
  fetchJobId: null,
  fetchTimer: null,
  fetchNotFoundCount: 0,
}

const charts = {}
const refs = {}
const CHART_EMPTY_TEXT = "当前范围没有可绘制的图表数据"
const CHART_LIBRARY_ERROR_TEXT = "图表组件未加载，当前网络可能屏蔽了外部图表库。"
const CHART_FONT_FAMILY = '"PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Segoe UI", sans-serif'

document.addEventListener("DOMContentLoaded", () => {
  captureRefs()
  bindEvents()
  hydrateVersion()
  const initialView = getQueryParam("view") || "overview"
  activateView(initialView)
  refreshProfiles().catch((error) => {
    console.error(error)
    showToast(error.message || "初始化失败", true)
    setStatus("初始化失败")
  })
})

function captureRefs() {
  const ids = [
    "versionChip",
    "profileSelect",
    "rangeSelect",
    "syncBtn",
    "reloadBtn",
    "openManagerBtn",
    "lastSyncText",
    "statusText",
    "heroTitle",
    "heroSubtitle",
    "heroMeta",
    "heroRecoveryScore",
    "heroRecoveryLabel",
    "heroRecoveryFootnote",
    "coverageGrid",
    "statsGrid",
    "correlationCards",
    "overviewHighlightGrid",
    "sleepTableWrap",
    "activityTableWrap",
    "recoveryTableWrap",
    "bodyMetricGrid",
    "vitalMetricGrid",
    "bodyTableWrap",
    "vitalTableWrap",
    "lifestyleMetricGrid",
    "foodTableWrap",
    "accountMetricGrid",
    "deviceTableWrap",
    "badgeTableWrap",
    "alarmTableWrap",
    "endpointTableWrap",
    "fileList",
    "scopeList",
    "familyGrid",
    "toast",
    "profileModal",
    "authModal",
    "createProfileForm",
    "newProfileName",
    "newClientId",
    "newClientSecret",
    "createProfileSubmit",
    "existingProfilesList",
    "authModalTitle",
    "authOpenLink",
    "authExchangeForm",
    "authRedirectValue",
    "authExchangeSubmit",
  ]
  ids.forEach((id) => {
    refs[id] = document.getElementById(id)
  })
}

function bindEvents() {
  refs.profileSelect?.addEventListener("change", async (event) => {
    state.selectedProfile = event.target.value || null
    syncQuery({ profile: state.selectedProfile })
    await loadDashboard()
    await loadProfileSummaries()
  })

  refs.rangeSelect?.addEventListener("change", () => {
    renderDashboard()
  })

  refs.syncBtn?.addEventListener("click", () => {
    startFetch().catch(handleAsyncError)
  })

  refs.reloadBtn?.addEventListener("click", () => {
    rebuildDashboard().catch(handleAsyncError)
  })

  refs.openManagerBtn?.addEventListener("click", () => {
    renderExistingProfilesList()
    openModal("profileModal")
  })

  refs.createProfileForm?.addEventListener("submit", (event) => {
    event.preventDefault()
    createProfile().catch(handleAsyncError)
  })

  refs.authExchangeForm?.addEventListener("submit", (event) => {
    event.preventDefault()
    submitAuthorization().catch(handleAsyncError)
  })

  refs.existingProfilesList?.addEventListener("click", (event) => {
    const target = event.target.closest("button[data-action]")
    if (!target) return
    const profile = target.dataset.profile
    const action = target.dataset.action
    if (!profile || !action) return

    if (action === "open") {
      state.selectedProfile = profile
      refs.profileSelect.value = profile
      syncQuery({ profile })
      closeModal("profileModal")
      loadDashboard().catch(handleAsyncError)
      loadProfileSummaries().catch(handleAsyncError)
      return
    }

    if (action === "authorize") {
      startAuthorization(profile).catch(handleAsyncError)
      return
    }

    if (action === "delete") {
      deleteProfile(profile).catch(handleAsyncError)
    }
  })

  document.querySelectorAll(".tab-button").forEach((button) => {
    button.addEventListener("click", () => {
      activateView(button.dataset.view)
    })
  })

  document.querySelectorAll("[data-close-modal]").forEach((element) => {
    element.addEventListener("click", () => {
      closeModal(element.dataset.closeModal)
    })
  })
}

function hydrateVersion() {
  if (window.FITBAUS_VERSION && refs.versionChip) {
    refs.versionChip.textContent = `本地缓存模式 · ${window.FITBAUS_VERSION}`
  }
}

async function refreshProfiles(preferredProfile) {
  const profiles = await apiRequest("/api/profiles")
  state.profiles = Array.isArray(profiles) ? profiles : []
  populateProfileSelect(preferredProfile)
  renderExistingProfilesList()

  if (!state.selectedProfile) {
    renderEmptyState()
    await loadProfileSummaries()
    return
  }

  await loadDashboard()
  await loadProfileSummaries()
}

function populateProfileSelect(preferredProfile) {
  const options = state.profiles
  refs.profileSelect.innerHTML = ""

  if (!options.length) {
    const option = document.createElement("option")
    option.value = ""
    option.textContent = "暂无档案"
    refs.profileSelect.appendChild(option)
    refs.profileSelect.disabled = true
    state.selectedProfile = null
    return
  }

  refs.profileSelect.disabled = false
  const requested = preferredProfile || getQueryParam("profile") || state.selectedProfile || options[0].name
  const nextProfile = options.find((item) => item.name === requested)?.name || options[0].name

  options.forEach((item) => {
    const option = document.createElement("option")
    option.value = item.name
    option.textContent = item.name
    refs.profileSelect.appendChild(option)
  })

  state.selectedProfile = nextProfile
  refs.profileSelect.value = nextProfile
  syncQuery({ profile: nextProfile })
}

async function loadDashboard() {
  if (!state.selectedProfile) {
    renderEmptyState()
    return
  }
  setStatus("正在读取本地缓存")
  const payload = await apiRequest(`/api/dashboard/${encodeURIComponent(state.selectedProfile)}`)
  state.dashboard = payload
  renderDashboard()
  setStatus("本地缓存已载入")
}

async function loadProfileSummaries() {
  const payload = await apiRequest("/api/profile-summaries")
  state.profileSummaries = Array.isArray(payload) ? payload : []
  renderFamily()
}

function renderDashboard() {
  if (!state.dashboard || !state.selectedProfile) {
    renderEmptyState()
    return
  }

  renderHero()
  renderCoverage()
  renderStats()
  scheduleActiveViewRender()
}

function renderActiveView() {
  if (!state.dashboard) return
  if (state.activeView === "overview") {
    renderCorrelations()
    renderOverviewHighlights()
    renderOverviewCharts()
    return
  }
  if (state.activeView === "sleep") {
    renderSleepView()
    return
  }
  if (state.activeView === "activity") {
    renderActivityView()
    return
  }
  if (state.activeView === "recovery") {
    renderRecoveryView()
    return
  }
  if (state.activeView === "body") {
    renderBodyView()
    return
  }
  if (state.activeView === "lifestyle") {
    renderLifestyleView()
    return
  }
  if (state.activeView === "account") {
    renderAccountView()
    return
  }
  if (state.activeView === "family") {
    renderFamily()
  }
}

function scheduleActiveViewRender() {
  if (!state.dashboard) return
  if (scheduleActiveViewRender.frameId) {
    window.cancelAnimationFrame(scheduleActiveViewRender.frameId)
  }
  scheduleActiveViewRender.frameId = window.requestAnimationFrame(() => {
    renderActiveView()
    resizeVisibleCharts()
  })
}

function renderEmptyState() {
  refs.heroTitle.textContent = "先创建并授权一个 Fitbit 档案"
  refs.heroSubtitle.textContent = "页面已重构为统一单页。创建并授权档案后，会自动同步 Fitbit 核心数据、本地快照以及补充体征接口。"
  refs.heroRecoveryScore.textContent = "--"
  refs.heroRecoveryLabel.textContent = "等待数据"
  refs.heroRecoveryFootnote.textContent = "完成授权并同步后，这里会显示恢复指数与快照状态。"
  refs.heroMeta.innerHTML = `<div class="empty-state">当前还没有可用档案。创建档案后，页面会从本地缓存读取睡眠、活动、恢复、体征、生活和账户数据。</div>`

  ;[
    "coverageGrid",
    "statsGrid",
    "overviewHighlightGrid",
    "correlationCards",
    "sleepTableWrap",
    "activityTableWrap",
    "recoveryTableWrap",
    "bodyMetricGrid",
    "vitalMetricGrid",
    "bodyTableWrap",
    "vitalTableWrap",
    "lifestyleMetricGrid",
    "foodTableWrap",
    "accountMetricGrid",
    "deviceTableWrap",
    "badgeTableWrap",
    "alarmTableWrap",
    "endpointTableWrap",
    "fileList",
    "scopeList",
  ].forEach((key) => {
    if (refs[key]) refs[key].innerHTML = `<div class="empty-state">当前没有可显示的数据。</div>`
  })

  refs.lastSyncText.textContent = "等待档案"
  setStatus("等待创建档案")
  destroyAllCharts()
}

function renderHero() {
  const profile = state.dashboard.profile || {}
  const overview = state.dashboard.overview || {}
  const coverage = state.dashboard.coverage || {}
  const snapshotStatus = state.dashboard.snapshot_status || {}
  const missingScopes = snapshotStatus.missing_scopes || []

  refs.heroTitle.textContent = `${profile.display_name || state.selectedProfile} 的 Fitbit 中文健康视图`
  refs.heroSubtitle.textContent =
    `统一页面优先读取本地缓存。最近记录日期：${overview.latest_date || "暂无"}，总追踪天数：${overview.tracked_days || 0}。`

  const meta = [
    ["档案", profile.id || state.selectedProfile],
    ["会员起始", profile.member_since || "未知"],
    ["设备数", profile.device_count || 0],
    ["徽章数", profile.badge_count || 0],
    ["睡眠目标", profile.sleep_goal_minutes ? `${profile.sleep_goal_minutes} 分钟` : "未缓存"],
    ["步数目标", profile.daily_steps_goal ? `${formatNumber(profile.daily_steps_goal)} 步` : "未缓存"],
    ["活动覆盖", coverage.activity?.count ? `${coverage.activity.count} 天` : "暂无"],
    ["快照状态", `${overview.snapshot_ok_count || 0}/${overview.snapshot_total_count || 0}`],
  ]

  refs.heroMeta.innerHTML = meta
    .map(([label, value]) => `<span class="hero-meta-pill">${escapeHtml(label)} · ${escapeHtml(String(value))}</span>`)
    .join("")

  refs.heroRecoveryScore.textContent = overview.recovery_score ?? "--"
  refs.heroRecoveryLabel.textContent = overview.recovery_label || "等待数据"
  refs.heroRecoveryFootnote.textContent =
    `最近快照：${formatDateTime(snapshotStatus.saved_at || overview.latest_sync_at)}。缺失 scope：${missingScopes.length ? missingScopes.join(" / ") : "无" }。`
  refs.lastSyncText.textContent = formatDateTime(snapshotStatus.saved_at || overview.latest_sync_at)
}

function renderCoverage() {
  const coverage = state.dashboard.coverage || {}
  const cards = [
    { key: "activity", label: "活动数据", tone: "green" },
    { key: "sleep", label: "睡眠数据", tone: "amber" },
    { key: "hrv", label: "HRV 数据", tone: "blue" },
    { key: "rhr", label: "静息心率", tone: "red" },
    { key: "daily", label: "合并视图", tone: "teal" },
    { key: "snapshot", label: "快照接口", tone: "blue" },
  ]

  refs.coverageGrid.innerHTML = cards
    .map(({ key, label, tone }) => {
      const item = coverage[key] || {}
      return `
        <article class="coverage-card" data-tone="${tone}">
          <div class="coverage-label">${escapeHtml(label)}</div>
          <div class="coverage-value">${formatNumber(item.count || 0)}</div>
          <div class="coverage-meta">
            起始：${escapeHtml(item.start_date || "暂无")}<br>
            截止：${escapeHtml(item.end_date || "暂无")}
          </div>
        </article>
      `
    })
    .join("")
}

function renderStats() {
  const stats = Array.isArray(state.dashboard.stats) ? state.dashboard.stats : []
  refs.statsGrid.innerHTML = stats
    .map((card) => {
      const tone = card.tone || "blue"
      const trend = card.trend || {}
      const trendDirection = trend.direction || "flat"
      const latest = formatMetricValue(card.key, card.latest, card.unit)
      const avg7 = formatMetricValue(card.key, card.avg7, card.unit)
      const avg30 = formatMetricValue(card.key, card.avg30, card.unit)
      return `
        <article class="metric-card" data-tone="${escapeHtml(tone)}">
          <div class="metric-topline">
            <div>
              <div class="metric-label">${escapeHtml(card.label || "--")}</div>
              <div class="metric-value">${latest}</div>
            </div>
            <span class="metric-badge ${escapeHtml(trendDirection)}">${escapeHtml(trendText(card, trend))}</span>
          </div>
          <div class="metric-meta">
            最新日期：${escapeHtml(card.latest_date || "暂无")}<br>
            ${goalText(card)}
          </div>
          <div class="metric-subline">
            <div>
              <span>近 7 天均值</span>
              <strong>${avg7}</strong>
            </div>
            <div>
              <span>近 30 天均值</span>
              <strong>${avg30}</strong>
            </div>
          </div>
        </article>
      `
    })
    .join("")
}

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
          <strong>${item.coefficient == null ? "--" : item.coefficient}</strong>
          <div class="metric-meta">
            ${escapeHtml(item.strength || "数据不足")}<br>
            重叠点数：${escapeHtml(String(item.points || 0))}
          </div>
        </div>
      `
    })
    .join("")
}

function renderOverviewHighlights() {
  const sections = state.dashboard.sections || {}
  const highlights = [
    ...(sections.body?.metrics || []).slice(0, 2),
    ...(sections.vitals?.metrics || []).slice(0, 2),
    ...(sections.lifestyle?.metrics || []).slice(0, 1),
    ...(sections.account?.metrics || []).slice(0, 1),
  ]
  renderDetailGrid(refs.overviewHighlightGrid, highlights)
}

function renderOverviewCharts() {
  const daily = getDailySeries()
  const weekly = Array.isArray(state.dashboard.charts?.weekly) ? state.dashboard.charts.weekly : []

  upsertChart("overviewTrendChart", {
    type: "bar",
    data: {
      labels: daily.map((row) => formatDate(row.date, "short")),
      datasets: [
        {
          type: "bar",
          label: "步数",
          data: daily.map((row) => row.steps ?? null),
          backgroundColor: "rgba(26, 115, 232, 0.22)",
          borderRadius: 8,
          yAxisID: "y",
        },
        {
          type: "line",
          label: "睡眠得分",
          data: daily.map((row) => row.sleep_score ?? null),
          borderColor: "#f9ab00",
          backgroundColor: "rgba(249, 171, 0, 0.18)",
          tension: 0.32,
          borderWidth: 2.5,
          pointRadius: 0,
          yAxisID: "y1",
        },
        {
          type: "line",
          label: "HRV",
          data: daily.map((row) => row.hrv ?? null),
          borderColor: "#0b9fa8",
          backgroundColor: "rgba(11, 159, 168, 0.16)",
          tension: 0.32,
          borderWidth: 2.5,
          pointRadius: 0,
          yAxisID: "y2",
        },
      ],
    },
    options: dualAxisOptions({
      y: { title: { display: true, text: "步数" } },
      y1: { position: "right", title: { display: true, text: "睡眠得分" }, min: 0, max: 100, grid: { drawOnChartArea: false } },
      y2: { display: false, min: 0 },
    }),
  })

  upsertChart("weeklyTrendChart", {
    type: "line",
    data: {
      labels: weekly.map((row) => formatPeriodLabel(row.period)),
      datasets: [
        {
          label: "周平均睡眠时长",
          data: weekly.map((row) => row.sleep_hours ?? null),
          borderColor: "#f9ab00",
          backgroundColor: "rgba(249, 171, 0, 0.18)",
          tension: 0.32,
          pointRadius: 3,
          pointHoverRadius: 5,
          fill: true,
          yAxisID: "y",
        },
        {
          label: "周平均 HRV",
          data: weekly.map((row) => row.hrv ?? null),
          borderColor: "#1a73e8",
          backgroundColor: "rgba(26, 115, 232, 0.16)",
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
  const last14 = daily.slice(-14)

  upsertChart("sleepTrendChart", {
    type: "line",
    data: {
      labels: daily.map((row) => formatDate(row.date, "short")),
      datasets: [
        {
          label: "睡眠得分",
          data: daily.map((row) => row.sleep_score ?? null),
          borderColor: "#1a73e8",
          backgroundColor: "rgba(26, 115, 232, 0.16)",
          tension: 0.32,
          borderWidth: 2.5,
          pointRadius: 0,
          yAxisID: "y",
        },
        {
          label: "睡眠时长",
          data: daily.map((row) => row.sleep_hours ?? null),
          borderColor: "#f9ab00",
          backgroundColor: "rgba(249, 171, 0, 0.18)",
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
      labels: last14.map((row) => formatDate(row.date, "short")),
      datasets: [
        {
          label: "深睡",
          data: last14.map((row) => minutesToHours(row.minutes_deep)),
          backgroundColor: "rgba(26, 115, 232, 0.75)",
          borderRadius: 8,
        },
        {
          label: "REM",
          data: last14.map((row) => minutesToHours(row.minutes_rem)),
          backgroundColor: "rgba(15, 157, 88, 0.7)",
          borderRadius: 8,
        },
        {
          label: "浅睡",
          data: last14.map((row) => minutesToHours(row.minutes_light)),
          backgroundColor: "rgba(249, 171, 0, 0.68)",
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
          backgroundColor: "rgba(15, 157, 88, 0.28)",
          borderRadius: 8,
          yAxisID: "y",
        },
        {
          type: "line",
          label: "活跃分钟",
          data: daily.map((row) => row.active_minutes ?? null),
          borderColor: "#0b9fa8",
          backgroundColor: "rgba(11, 159, 168, 0.18)",
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
          borderColor: "#d93025",
          backgroundColor: "rgba(217, 48, 37, 0.16)",
          tension: 0.32,
          pointRadius: 0,
          borderWidth: 2.5,
          yAxisID: "y",
        },
        {
          label: "消耗热量",
          data: daily.map((row) => row.calories_out ?? null),
          borderColor: "#f9ab00",
          backgroundColor: "rgba(249, 171, 0, 0.16)",
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

  renderTable(refs.activityTableWrap, [
    { key: "date", label: "日期" },
    { key: "steps", label: "步数" },
    { key: "active_minutes", label: "活跃分钟" },
    { key: "active_zone_minutes", label: "燃脂区分钟" },
    { key: "calories_out", label: "热量" },
    { key: "exercise_examples", label: "活动样例" },
  ], state.dashboard.tables?.activity || [], {
    steps: formatNumber,
    active_minutes: formatMinutes,
    active_zone_minutes: formatMinutes,
    calories_out: (value) => value == null ? "--" : `${formatNumber(value)} kcal`,
  })
}

function renderRecoveryView() {
  const daily = getDailySeries()

  upsertChart("recoveryTrendChart", {
    type: "line",
    data: {
      labels: daily.map((row) => formatDate(row.date, "short")),
      datasets: [
        {
          label: "HRV",
          data: daily.map((row) => row.hrv ?? null),
          borderColor: "#1a73e8",
          backgroundColor: "rgba(26, 115, 232, 0.16)",
          tension: 0.32,
          borderWidth: 2.5,
          pointRadius: 0,
          yAxisID: "y",
        },
        {
          label: "静息心率",
          data: daily.map((row) => row.rhr ?? null),
          borderColor: "#d93025",
          backgroundColor: "rgba(217, 48, 37, 0.16)",
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
          borderColor: "#0b9fa8",
          backgroundColor: "rgba(11, 159, 168, 0.38)",
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
  ], state.dashboard.tables?.foods || [], {
    calories: (value) => value == null ? "--" : `${formatNumber(value)} kcal`,
  })
}

function renderAccountView() {
  const snapshotStatus = state.dashboard.snapshot_status || {}
  const files = state.dashboard.files || {}
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
    { key: "scope", label: "Scope" },
    { key: "status", label: "状态" },
    { key: "updated_at", label: "更新时间" },
  ], state.dashboard.tables?.endpoints || [], {
    updated_at: (value) => formatDateTime(value),
  })

  refs.fileList.innerHTML = Object.entries(files)
    .map(([key, value]) => {
      return `
        <div class="file-item">
          <strong>${escapeHtml(prettyFileKey(key))}</strong>
          <small>${escapeHtml(fileHint(key))}</small>
          <code>${escapeHtml(value || "--")}</code>
        </div>
      `
    })
    .join("")

  refs.scopeList.innerHTML = `
    <div class="scope-chip">
      <strong>快照概况</strong>
      <small>已缓存 ${fetchSummary.ok || 0}/${fetchSummary.total || 0} 个接口，缺失 scope ${missingScopes.length} 项。</small>
      <div class="scope-row">
        ${(grantedScopes.length ? grantedScopes : ["暂无已授权 scope"]).map((scope) => `<span class="hero-meta-pill">${escapeHtml(scope)}</span>`).join("")}
      </div>
    </div>
    <div class="scope-chip">
      <strong>仍需补齐的 scope</strong>
      <small>重新授权后，页面会在下一次同步时自动补抓这些接口。</small>
      <div class="scope-row">
        ${(missingScopes.length ? missingScopes : ["已覆盖当前页面所需 scope"]).map((scope) => `<span class="hero-meta-pill scope-missing">${escapeHtml(scope)}</span>`).join("")}
      </div>
    </div>
    <div class="scope-chip">
      <strong>目标 scope</strong>
      <small>为了支持体征、生活和账户页，应用会请求这些 Fitbit 读取范围。</small>
      <div class="scope-row">
        ${(requestedScopes.length ? requestedScopes : ["暂无配置"]).map((scope) => `<span class="hero-meta-pill">${escapeHtml(scope)}</span>`).join("")}
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

function renderFamily() {
  const cards = state.profileSummaries
  if (!cards.length) {
    refs.familyGrid.innerHTML = `<div class="empty-state">当前没有档案可对比。</div>`
    return
  }

  refs.familyGrid.innerHTML = cards
    .map((card) => {
      return `
        <article class="family-profile-card">
          <div class="family-head">
            <div>
              <div class="family-label">${escapeHtml(card.display_name || card.id || "--")}</div>
              <div class="family-meta">最近记录：${escapeHtml(card.latest_date || "暂无")}</div>
            </div>
            <button class="button button-light" type="button" data-jump-profile="${escapeHtml(card.id || "")}">查看</button>
          </div>
          <div>
            <div class="family-label">恢复指数</div>
            <div class="family-score">${card.recovery_score ?? "--"}</div>
            <div class="family-meta">${escapeHtml(card.recovery_label || "等待数据")}</div>
          </div>
          <div class="family-kpis">
            <div class="family-kpi">
              <span>睡眠得分</span>
              <strong>${card.sleep_score == null ? "--" : formatNumber(card.sleep_score, 1)}</strong>
            </div>
            <div class="family-kpi">
              <span>步数</span>
              <strong>${card.steps == null ? "--" : formatNumber(card.steps)}</strong>
            </div>
            <div class="family-kpi">
              <span>HRV</span>
              <strong>${card.hrv == null ? "--" : formatNumber(card.hrv, 1)}</strong>
            </div>
            <div class="family-kpi">
              <span>静息心率</span>
              <strong>${card.rhr == null ? "--" : formatNumber(card.rhr)}</strong>
            </div>
          </div>
        </article>
      `
    })
    .join("")

  refs.familyGrid.querySelectorAll("[data-jump-profile]").forEach((button) => {
    button.addEventListener("click", async () => {
      const profile = button.dataset.jumpProfile
      if (!profile) return
      state.selectedProfile = profile
      refs.profileSelect.value = profile
      syncQuery({ profile })
      await loadDashboard()
      activateView("overview")
    })
  })
}

async function createProfile() {
  const profileName = refs.newProfileName.value.trim()
  const clientId = refs.newClientId.value.trim()
  const clientSecret = refs.newClientSecret.value.trim()

  if (!profileName || !clientId || !clientSecret) {
    showToast("请把新档案信息填完整。", true)
    return
  }

  setButtonState(refs.createProfileSubmit, true, "创建中...")
  try {
    await apiRequest("/api/create-profile", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ profileName, clientId, clientSecret }),
    })
    refs.createProfileForm.reset()
    showToast(`档案 ${profileName} 已创建，下一步继续授权。`)
    await refreshProfiles(profileName)
    await startAuthorization(profileName)
  } finally {
    setButtonState(refs.createProfileSubmit, false, "创建档案")
  }
}

function renderExistingProfilesList() {
  if (!state.profiles.length) {
    refs.existingProfilesList.innerHTML = `<div class="empty-state">还没有任何档案。</div>`
    return
  }

  refs.existingProfilesList.innerHTML = state.profiles
    .map((profile) => {
      return `
        <article class="profile-row">
          <div class="profile-row-head">
            <div>
              <h5>${escapeHtml(profile.name)}</h5>
              <p>创建时间：${escapeHtml(profile.created || "未知")}</p>
            </div>
            <span class="hero-meta-pill">${state.selectedProfile === profile.name ? "当前查看" : "可切换"}</span>
          </div>
          <div class="profile-actions">
            <button class="button button-light" type="button" data-action="open" data-profile="${escapeHtml(profile.name)}">打开</button>
            <button class="button button-secondary" type="button" data-action="authorize" data-profile="${escapeHtml(profile.name)}">授权</button>
            <button class="button button-secondary" type="button" data-action="delete" data-profile="${escapeHtml(profile.name)}">删除</button>
          </div>
        </article>
      `
    })
    .join("")
}

async function startAuthorization(profileName) {
  const payload = await apiRequest(`/api/authorize/${encodeURIComponent(profileName)}`)
  state.authProfile = profileName
  refs.authModalTitle.textContent = `授权 Fitbit 档案：${profileName}`
  refs.authOpenLink.href = payload.auth_url || "#"
  refs.authRedirectValue.value = ""
  closeModal("profileModal")
  openModal("authModal")
}

async function submitAuthorization() {
  if (!state.authProfile) {
    showToast("没有要授权的档案。", true)
    return
  }
  const redirectUrl = refs.authRedirectValue.value.trim()
  if (!redirectUrl) {
    showToast("请粘贴回调 URL 或 code。", true)
    return
  }
  setButtonState(refs.authExchangeSubmit, true, "提交中...")
  try {
    await apiRequest("/api/authorize-exchange", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        profileName: state.authProfile,
        redirectUrl,
      }),
    })
    closeModal("authModal")
    showToast(`档案 ${state.authProfile} 授权完成，现在可以同步数据。`)
    await refreshProfiles(state.authProfile)
  } finally {
    setButtonState(refs.authExchangeSubmit, false, "提交授权结果")
  }
}

async function deleteProfile(profileName) {
  if (!window.confirm(`确认删除档案 ${profileName} 吗？本地 CSV、缓存和授权文件都会被清掉。`)) {
    return
  }
  await apiRequest("/api/delete-profile", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ profileName }),
  })
  showToast(`档案 ${profileName} 已删除。`)
  if (state.selectedProfile === profileName) {
    state.selectedProfile = null
  }
  await refreshProfiles()
}

async function rebuildDashboard() {
  if (!state.selectedProfile) {
    showToast("请先选择档案。", true)
    return
  }
  setStatus("正在重建本地缓存")
  await apiRequest(`/api/rebuild-dashboard/${encodeURIComponent(state.selectedProfile)}`, {
    method: "POST",
  })
  await loadDashboard()
  await loadProfileSummaries()
  showToast("本地缓存已重建。")
}

async function startFetch() {
  if (!state.selectedProfile) {
    showToast("请先选择档案。", true)
    return
  }

  const payload = await apiRequest("/api/fetch-data", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ profile: state.selectedProfile }),
  })

  state.fetchJobId = payload.job_id
  state.fetchNotFoundCount = 0
  setButtonState(refs.syncBtn, true, "同步中...")
  setStatus("已发起同步任务")
  pollFetchStatus()
}

function pollFetchStatus() {
  if (!state.fetchJobId) return
  clearInterval(state.fetchTimer)
  state.fetchTimer = setInterval(async () => {
    try {
      const payload = await apiRequest(`/api/fetch-status/${encodeURIComponent(state.fetchJobId)}`, { ignore404: true })
      if (!payload) {
        state.fetchNotFoundCount += 1
        if (state.fetchNotFoundCount >= 3) {
          clearInterval(state.fetchTimer)
          state.fetchTimer = null
          state.fetchJobId = null
          setButtonState(refs.syncBtn, false, "同步 Fitbit")
          setStatus("同步任务状态已清理")
        }
        return
      }
      state.fetchNotFoundCount = 0
      updateFetchStatus(payload)
      if (["completed", "failed", "timeout", "error", "cancelled"].includes(payload.status)) {
        clearInterval(state.fetchTimer)
        state.fetchTimer = null
        state.fetchJobId = null
        setButtonState(refs.syncBtn, false, "同步 Fitbit")
        if (payload.status === "completed") {
          setStatus("同步完成，正在刷新缓存")
          await loadDashboard()
          await loadProfileSummaries()
          showToast("Fitbit 数据已同步完成。")
        } else {
          showToast(payload.error || "同步失败。", true)
          setStatus("同步失败")
        }
      }
    } catch (error) {
      clearInterval(state.fetchTimer)
      state.fetchTimer = null
      state.fetchJobId = null
      setButtonState(refs.syncBtn, false, "同步 Fitbit")
      showToast(error.message || "轮询同步状态失败。", true)
      setStatus("同步状态读取失败")
    }
  }, 2000)
}

function updateFetchStatus(payload) {
  const percent = payload.progress != null ? Math.round(payload.progress * 100) : null
  const parts = []
  if (payload.status) parts.push(statusLabel(payload.status))
  if (percent != null && Number.isFinite(percent)) parts.push(`${percent}%`)
  if (payload.message) parts.push(payload.message)
  if (payload.current_csv) parts.push(payload.current_csv)
  if (payload.throttle_active) {
    parts.push(payload.throttle_mmss ? `限流倒计时 ${payload.throttle_mmss}` : "Fitbit 限流中")
  }
  setStatus(parts.join(" · "))
}

function activateView(viewName) {
  state.activeView = viewName || "overview"
  document.querySelectorAll(".tab-button").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === state.activeView)
  })
  document.querySelectorAll("[data-view-panel]").forEach((panel) => {
    panel.classList.toggle("active", panel.dataset.viewPanel === state.activeView)
  })
  syncQuery({ view: state.activeView })
  scheduleActiveViewRender()
}

function openModal(id) {
  const element = refs[id]
  if (!element) return
  element.classList.remove("hidden")
  element.setAttribute("aria-hidden", "false")
}

function closeModal(id) {
  const element = refs[id]
  if (!element) return
  element.classList.add("hidden")
  element.setAttribute("aria-hidden", "true")
}

function renderTable(container, columns, rows, formatters = {}) {
  if (!container) return
  if (!rows.length) {
    container.innerHTML = `<div class="empty-state">当前没有可显示的数据。</div>`
    return
  }

  const head = columns.map((column) => `<th>${escapeHtml(column.label)}</th>`).join("")
  const body = rows
    .map((row) => {
      const cells = columns
        .map((column) => {
          const formatter = formatters[column.key]
          const raw = row[column.key]
          const value = formatter ? formatter(raw, row) : raw
          return `<td>${escapeHtml(value == null || value === "" ? "--" : String(value))}</td>`
        })
        .join("")
      return `<tr>${cells}</tr>`
    })
    .join("")

  container.innerHTML = `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`
}

function getDailySeries() {
  const source = Array.isArray(state.dashboard?.charts?.daily) ? state.dashboard.charts.daily : []
  const windowSize = Number(refs.rangeSelect.value || 30)
  return source.slice(-windowSize)
}

function upsertChart(canvasId, config) {
  const canvas = document.getElementById(canvasId)
  if (!canvas) return
  if (charts[canvasId]) {
    charts[canvasId].destroy()
    delete charts[canvasId]
  }

  if (typeof Chart === "undefined") {
    toggleChartEmptyState(canvas, "library")
    clearCanvas(canvas)
    return
  }

  const hasData = chartHasData(config.data)
  toggleChartEmptyState(canvas, hasData ? null : "data")
  if (!hasData) {
    clearCanvas(canvas)
    return
  }

  config.options = {
    responsive: true,
    maintainAspectRatio: false,
    normalized: true,
    animation: {
      duration: 520,
      easing: "easeOutCubic",
    },
    layout: {
      padding: { top: 6, right: 4, bottom: 0, left: 0 },
    },
    interaction: {
      mode: config.type === "scatter" ? "nearest" : "index",
      intersect: false,
    },
    elements: {
      line: {
        borderCapStyle: "round",
        borderJoinStyle: "round",
      },
      point: {
        hoverRadius: 6,
        hitRadius: 16,
      },
      bar: {
        borderRadius: 10,
        borderSkipped: false,
      },
    },
    ...config.options,
    plugins: {
      legend: {
        position: "top",
        align: "start",
        labels: {
          usePointStyle: true,
          boxWidth: 10,
          padding: 16,
          color: "#4f5d75",
          font: {
            family: CHART_FONT_FAMILY,
            size: 12,
            weight: "600",
          },
        },
      },
      tooltip: {
        backgroundColor: "rgba(18, 33, 61, 0.94)",
        padding: 14,
        cornerRadius: 14,
        boxPadding: 5,
        displayColors: true,
        titleColor: "#f8fbff",
        bodyColor: "#e8eef8",
        titleFont: {
          family: CHART_FONT_FAMILY,
          weight: "700",
        },
        bodyFont: {
          family: CHART_FONT_FAMILY,
        },
      },
      ...(config.options?.plugins || {}),
    },
    scales: {
      ...Object.fromEntries(
        Object.entries(config.options?.scales || {}).map(([key, value]) => [
          key,
          {
            border: { display: false },
            grid: {
              color: "rgba(26, 115, 232, 0.08)",
              drawTicks: false,
              tickLength: 0,
            },
            ticks: {
              color: "#66748c",
              padding: 8,
              maxRotation: 0,
            },
            title: {
              color: "#66748c",
              font: {
                family: CHART_FONT_FAMILY,
                size: 12,
                weight: "600",
              },
            },
            ...value,
          },
        ])
      ),
    },
  }
  charts[canvasId] = new Chart(canvas.getContext("2d"), config)
}

function chartHasData(data) {
  const datasets = Array.isArray(data?.datasets) ? data.datasets : []
  return datasets.some((dataset) => {
    const points = Array.isArray(dataset?.data) ? dataset.data : []
    return points.some((point) => {
      if (point == null) return false
      if (typeof point === "object") {
        return point.x != null && point.y != null
      }
      return Number.isFinite(Number(point))
    })
  })
}

function toggleChartEmptyState(canvas, reason) {
  const card = canvas.closest(".chart-card")
  if (!card) return
  let overlay = card.querySelector(".chart-empty")
  if (!overlay) {
    overlay = document.createElement("div")
    overlay.className = "chart-empty hidden"
    card.appendChild(overlay)
  }
  overlay.textContent = reason === "library" ? CHART_LIBRARY_ERROR_TEXT : CHART_EMPTY_TEXT
  overlay.classList.toggle("hidden", !reason)
  canvas.classList.toggle("chart-hidden", Boolean(reason))
}

function clearCanvas(canvas) {
  const context = canvas.getContext("2d")
  if (context) {
    context.clearRect(0, 0, canvas.width, canvas.height)
  }
}

function resizeVisibleCharts() {
  Object.entries(charts).forEach(([key, chart]) => {
    const canvas = document.getElementById(key)
    if (!canvas || !chart) return
    if (canvas.offsetParent !== null) {
      chart.resize()
    }
  })
}

function dualAxisOptions(scales) {
  return { scales }
}

function stackedOptions(unitLabel) {
  return {
    scales: {
      x: { stacked: true },
      y: {
        stacked: true,
        title: { display: true, text: unitLabel },
      },
    },
  }
}

function destroyAllCharts() {
  Object.values(charts).forEach((chart) => chart.destroy())
  Object.keys(charts).forEach((key) => delete charts[key])
}

async function apiRequest(path, options = {}) {
  const { ignore404, ...fetchOptions } = options
  const response = await fetch(path, fetchOptions)

  if (ignore404 && response.status === 404) {
    return null
  }

  const contentType = response.headers.get("content-type") || ""
  const payload = contentType.includes("application/json") ? await response.json() : await response.text()
  if (!response.ok) {
    const message = typeof payload === "string" ? payload : payload.error || payload.message || "请求失败"
    throw new Error(message)
  }
  return payload
}

function setStatus(text) {
  refs.statusText.textContent = text || "等待数据"
}

function setButtonState(button, disabled, label) {
  if (!button) return
  button.disabled = disabled
  if (label) button.textContent = label
}

function showToast(message, isError = false) {
  if (!refs.toast) return
  refs.toast.textContent = message
  refs.toast.classList.remove("hidden")
  refs.toast.style.background = isError ? "rgba(217, 48, 37, 0.95)" : "rgba(22, 37, 61, 0.96)"
  clearTimeout(showToast.timer)
  showToast.timer = setTimeout(() => {
    refs.toast.classList.add("hidden")
  }, 3200)
}

function handleAsyncError(error) {
  console.error(error)
  showToast(error.message || "操作失败", true)
  setStatus(error.message || "操作失败")
}

function goalText(card) {
  if (card.key === "sleep_hours" && card.goal) {
    return `睡眠目标：${formatNumber(card.goal / 60, 1)} 小时`
  }
  if (card.key === "steps" && card.goal) {
    return `步数目标：${formatNumber(card.goal)} 步`
  }
  return "目标：使用最近 7/30 天对比"
}

function trendText(card, trend) {
  if (!trend || trend.delta == null) return "趋势待定"
  if (trend.direction === "flat") return "基本持平"
  const sign = trend.delta > 0 ? "+" : ""
  const delta = formatNumber(trend.delta, 1)
  const percent = trend.percent == null ? "" : ` (${sign}${formatNumber(trend.percent, 1)}%)`
  return `${sign}${delta}${card.unit || ""}${percent}`
}

function formatMetricValue(key, value, unit) {
  if (value == null) return "--"
  if (key === "sleep_hours") return `${formatNumber(value, 1)} 小时`
  if (key === "sleep_score") return `${formatNumber(value, 1)} 分`
  if (key === "hrv") return `${formatNumber(value, 1)} ms`
  if (key === "rhr") return `${formatNumber(value)} bpm`
  if (key === "calories_out") return `${formatNumber(value)} kcal`
  return `${formatNumber(value)} ${unit || ""}`.trim()
}

function formatDetailValue(value) {
  if (value == null || value === "") return "--"
  if (typeof value === "number") {
    return Number.isInteger(value) ? formatNumber(value) : formatNumber(value, 1)
  }
  return String(value)
}

function formatMinutes(value) {
  if (value == null) return "--"
  return `${formatNumber(value)} 分钟`
}

function minutesToHours(value) {
  if (value == null) return null
  return Number((value / 60).toFixed(1))
}

function formatNumber(value, digits = 0) {
  if (value == null || value === "" || Number.isNaN(Number(value))) return "--"
  const formatter = new Intl.NumberFormat("zh-CN", {
    maximumFractionDigits: digits,
    minimumFractionDigits: digits > 0 ? digits : 0,
  })
  return formatter.format(Number(value))
}

function formatDate(value, mode = "long") {
  if (!value) return "暂无"
  const date = new Date(`${String(value).slice(0, 10)}T00:00:00`)
  if (Number.isNaN(date.getTime())) return String(value)
  const options = mode === "short"
    ? { month: "numeric", day: "numeric" }
    : { year: "numeric", month: "numeric", day: "numeric" }
  return new Intl.DateTimeFormat("zh-CN", options).format(date)
}

function formatDateTime(value) {
  if (!value) return "暂无"
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return String(value)
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date)
}

function formatPeriodLabel(value) {
  if (!value) return "暂无"
  const weekMatch = String(value).match(/^(\d{4})-W(\d{2})$/)
  if (weekMatch) {
    return `W${weekMatch[2]}`
  }
  const monthMatch = String(value).match(/^(\d{4})-(\d{2})$/)
  if (monthMatch) {
    return `${Number(monthMatch[2])} 月`
  }
  return String(value)
}

function statusLabel(status) {
  const map = {
    queued: "排队中",
    running: "同步中",
    completed: "同步完成",
    failed: "同步失败",
    timeout: "同步超时",
    error: "同步出错",
    cancelled: "已取消",
  }
  return map[status] || status
}

function prettyFileKey(key) {
  const map = {
    dashboard_cache: "仪表盘缓存",
    profile_snapshot: "Fitbit 元数据快照",
    tokens: "授权 tokens",
    client: "Client 凭据",
    activity_csv: "活动 CSV",
    sleep_csv: "睡眠 CSV",
    hrv_csv: "HRV CSV",
    rhr_csv: "静息心率 CSV",
  }
  return map[key] || key
}

function fileHint(key) {
  const map = {
    dashboard_cache: "前端直接读取这个统一缓存",
    profile_snapshot: "补充 profile、goals、vitals、devices、foods 等接口数据",
    tokens: "OAuth token 文件",
    client: "Fitbit 应用凭据",
    activity_csv: "原始活动历史缓存",
    sleep_csv: "原始睡眠历史缓存",
    hrv_csv: "原始 HRV 历史缓存",
    rhr_csv: "原始静息心率历史缓存",
  }
  return map[key] || "本地文件"
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;")
}

function getQueryParam(name) {
  const params = new URLSearchParams(window.location.search)
  return params.get(name)
}

function syncQuery(values) {
  const params = new URLSearchParams(window.location.search)
  Object.entries(values).forEach(([key, value]) => {
    if (value == null || value === "") {
      params.delete(key)
    } else {
      params.set(key, value)
    }
  })
  const next = `${window.location.pathname}?${params.toString()}`
  window.history.replaceState({}, "", next.endsWith("?") ? window.location.pathname : next)
}
