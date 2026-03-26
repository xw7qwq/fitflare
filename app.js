const state = {
  profiles: [],
  selectedProfile: null,
  dashboard: null,
  viewModel: null,
  profileSummaries: [],
  admin: {
    configured: false,
    authenticated: false,
    csrfToken: null,
  },
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
const DEFAULT_RANGE = "30"
const VALID_RANGES = new Set(["14", "30", "90"])
const VIEW_ORDER = ["overview", "sleep", "activity", "recovery", "body", "lifestyle", "account", "family"]
const METRIC_VIEW_MAP = {
  sleep_score: "sleep",
  sleep_hours: "sleep",
  steps: "activity",
  active_minutes: "activity",
  active_zone_minutes: "activity",
  hrv: "recovery",
  rhr: "recovery",
  calories_out: "activity",
}
const VIEW_META = {
  overview: { label: "总览", kicker: "Overview", summary: "趋势、相关性和重点信号", tone: "blue" },
  sleep: { label: "睡眠", kicker: "Sleep", summary: "睡眠质量、时长和阶段结构", tone: "blue" },
  activity: { label: "活动", kicker: "Activity", summary: "步数、活跃分钟和消耗热量", tone: "green" },
  recovery: { label: "恢复", kicker: "Recovery", summary: "HRV、RHR 和睡眠联动", tone: "teal" },
  body: { label: "体征", kicker: "Body", summary: "体重、BMI、体脂与补充 vitals", tone: "amber" },
  lifestyle: { label: "生活", kicker: "Lifestyle", summary: "饮食、饮水和营养目标", tone: "amber" },
  account: { label: "账户", kicker: "Account", summary: "设备、scope 和缓存状态", tone: "red" },
  family: { label: "档案", kicker: "Profiles", summary: "多档案横向对比", tone: "blue" },
}

document.addEventListener("DOMContentLoaded", () => {
  captureRefs()
  bindEvents()
  hydrateVersion()
  const initialRoute = getRouteState()
  if (refs.rangeSelect && VALID_RANGES.has(initialRoute.range || "")) {
    refs.rangeSelect.value = initialRoute.range
  }
  activateView(initialRoute.view || "overview", { updateHistory: false, scroll: false })
  initializeApp().catch((error) => {
    console.error(error)
    showToast(error.message || "初始化失败", true)
    setStatus("初始化失败")
  })
})

async function initializeApp() {
  await refreshAdminSession({ silent: true })
  await refreshProfiles()
}

function captureRefs() {
  const ids = [
    "versionChip",
    "adminModeChip",
    "profileSelect",
    "rangeSelect",
    "adminLoginBtn",
    "adminLogoutBtn",
    "syncBtn",
    "reloadBtn",
    "openManagerBtn",
    "lastSyncText",
    "statusText",
    "heroTitle",
    "heroSubtitle",
    "heroKpis",
    "heroMeta",
    "heroRecoveryScore",
    "heroRecoveryLabel",
    "heroRecoveryFootnote",
    "quickNavGrid",
    "healthDigestGrid",
    "activeViewSummary",
    "coverageGrid",
    "statsGrid",
    "correlationCards",
    "overviewHighlightGrid",
    "sleepTableWrap",
    "activityContextGrid",
    "activityLogTableWrap",
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
    "adminModal",
    "profileModal",
    "authModal",
    "adminPasswordForm",
    "adminPasswordInput",
    "adminLoginSubmit",
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
    syncRoute({ profile: state.selectedProfile }, { mode: "push" })
    await loadDashboard()
    await loadProfileSummaries()
  })

  refs.rangeSelect?.addEventListener("change", () => {
    syncRoute({ range: refs.rangeSelect.value || DEFAULT_RANGE }, { mode: "push" })
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

  refs.adminLoginBtn?.addEventListener("click", () => {
    openAdminModal()
  })

  refs.adminLogoutBtn?.addEventListener("click", () => {
    logoutAdmin().catch(handleAsyncError)
  })

  refs.adminPasswordForm?.addEventListener("submit", (event) => {
    event.preventDefault()
    loginAdmin().catch(handleAsyncError)
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
      syncRoute({ profile }, { mode: "push" })
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
      activateView(button.dataset.view, { historyMode: "push", scroll: true })
    })
    button.addEventListener("keydown", (event) => {
      handleTabKeydown(event)
    })
  })

  ;[refs.quickNavGrid, refs.healthDigestGrid, refs.statsGrid].forEach((container) => {
    container?.addEventListener("click", (event) => {
      const target = event.target.closest("[data-jump-view]")
      if (!target) return
      activateView(target.dataset.jumpView, { historyMode: "push", scroll: true })
    })
  })

  document.querySelectorAll("[data-close-modal]").forEach((element) => {
    element.addEventListener("click", () => {
      closeModal(element.dataset.closeModal)
    })
  })

  window.addEventListener("resize", queueVisibleChartResize)
  window.addEventListener("popstate", () => {
    applyRouteState().catch(handleAsyncError)
  })
}

function hydrateVersion() {
  if (window.FITBAUS_VERSION && refs.versionChip) {
    refs.versionChip.textContent = `本地缓存模式 · ${window.FITBAUS_VERSION}`
  }
}

async function refreshAdminSession({ silent = false } = {}) {
  try {
    const payload = await apiRequest("/api/admin/session", { skipAdminHandling: true })
    applyAdminSession(payload)
    return payload
  } catch (error) {
    applyAdminSession({ configured: false, authenticated: false, csrf_token: null })
    if (!silent) throw error
    return null
  }
}

function applyAdminSession(payload = {}) {
  state.admin.configured = Boolean(payload.configured)
  state.admin.authenticated = Boolean(payload.authenticated)
  state.admin.csrfToken = state.admin.authenticated ? payload.csrf_token || null : null
  renderAdminControls()
}

function renderAdminControls() {
  const { configured, authenticated } = state.admin

  if (refs.adminModeChip) {
    refs.adminModeChip.dataset.mode = !configured ? "disabled" : authenticated ? "admin" : "public"
    refs.adminModeChip.textContent = !configured
      ? "管理未配置"
      : authenticated
        ? "管理员模式"
        : "公开只读模式"
  }

  if (refs.adminLoginBtn) {
    refs.adminLoginBtn.classList.toggle("hidden", !configured || authenticated)
    refs.adminLoginBtn.disabled = !configured
    refs.adminLoginBtn.textContent = configured ? "管理员登录" : "管理未配置"
  }

  refs.adminLogoutBtn?.classList.toggle("hidden", !authenticated)
  refs.openManagerBtn?.classList.toggle("hidden", !authenticated)
  refs.syncBtn?.classList.toggle("hidden", !authenticated)
  refs.reloadBtn?.classList.toggle("hidden", !authenticated)

  if (!authenticated) {
    closeModal("profileModal")
    closeModal("authModal")
  }

  renderExistingProfilesList()
}

function openAdminModal() {
  if (!state.admin.configured) {
    showToast("管理员口令尚未配置，当前仅支持公开只读。", true)
    return
  }
  if (state.admin.authenticated) return
  if (refs.adminPasswordForm) refs.adminPasswordForm.reset()
  openModal("adminModal")
  window.setTimeout(() => {
    refs.adminPasswordInput?.focus()
  }, 40)
}

async function loginAdmin() {
  const password = refs.adminPasswordInput?.value?.trim() || ""
  if (!password) {
    showToast("请输入管理员口令。", true)
    return
  }
  setButtonState(refs.adminLoginSubmit, true, "登录中...")
  try {
    const payload = await apiRequest("/api/admin/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password }),
      skipAdminHandling: true,
    })
    applyAdminSession(payload)
    closeModal("adminModal")
    showToast("已进入管理员模式。")
    setStatus("管理员模式已启用")
  } finally {
    setButtonState(refs.adminLoginSubmit, false, "进入管理模式")
    if (refs.adminPasswordInput) refs.adminPasswordInput.value = ""
  }
}

async function logoutAdmin() {
  const payload = await apiRequest("/api/admin/logout", {
    method: "POST",
    requireAdmin: true,
  })
  applyAdminSession(payload)
  closeModal("adminModal")
  showToast("已退出管理员模式。")
  setStatus("公开只读模式")
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
  const requested = preferredProfile || getRouteState().profile || state.selectedProfile || options[0].name
  const nextProfile = options.find((item) => item.name === requested)?.name || options[0].name

  options.forEach((item) => {
    const option = document.createElement("option")
    option.value = item.name
    option.textContent = item.name
    refs.profileSelect.appendChild(option)
  })

  state.selectedProfile = nextProfile
  refs.profileSelect.value = nextProfile
  syncRoute({ profile: nextProfile }, { mode: "replace" })
}

async function loadDashboard() {
  if (!state.selectedProfile) {
    state.dashboard = null
    state.viewModel = null
    renderEmptyState()
    return
  }
  setStatus("正在读取本地缓存")
  const payload = await apiRequest(`/api/dashboard/${encodeURIComponent(state.selectedProfile)}`)
  state.dashboard = payload
  state.viewModel = normalizeDashboard(payload)
  renderDashboard()
  setStatus("本地缓存已载入")
}

async function loadProfileSummaries() {
  const payload = await apiRequest("/api/profile-summaries")
  state.profileSummaries = Array.isArray(payload) ? payload : []
  renderFamily()
}

function renderDashboard() {
  if (!state.dashboard || !state.selectedProfile || !state.viewModel) {
    renderEmptyState()
    return
  }

  renderHero()
  renderQuickNav()
  renderHealthDigest()
  renderActiveViewSummary()
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
    queueVisibleChartResize()
  })
}

function renderEmptyState() {
  refs.heroTitle.textContent = "先创建并授权一个 Fitbit 档案"
  refs.heroSubtitle.textContent = "页面会先读取本地缓存，再把 Fitbit 数据整理成统一视图。创建并授权档案后会自动同步。"
  if (refs.heroKpis) refs.heroKpis.innerHTML = ""
  refs.heroRecoveryScore.textContent = "--"
  refs.heroRecoveryLabel.textContent = "等待数据"
  refs.heroRecoveryFootnote.textContent = "完成授权并同步后，这里会显示恢复指数与快照状态。"
  refs.heroMeta.innerHTML = `<div class="empty-state">当前还没有可用档案。创建档案后，页面会从本地缓存读取睡眠、活动、恢复、体征、生活和账户数据。</div>`
  if (refs.activeViewSummary) {
    refs.activeViewSummary.innerHTML = `<div class="empty-state">选择档案后，这里会给出当前页面的重点说明、关键指标和最近范围。</div>`
  }

  ;[
    "quickNavGrid",
    "healthDigestGrid",
    "coverageGrid",
    "statsGrid",
    "overviewHighlightGrid",
    "correlationCards",
    "sleepTableWrap",
    "activityContextGrid",
    "activityLogTableWrap",
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
  const { profile, overview, coverage, snapshotStatus, heroKpis } = state.viewModel
  const missingScopes = snapshotStatus.missing_scopes || []

  refs.heroTitle.textContent = `${profile.display_name || state.selectedProfile} 的 Fitbit 中文健康视图`
  refs.heroSubtitle.textContent =
    `统一页面先读本地缓存，再把 Fitbit 数据整理成稳定视图。最近记录日期：${overview.latest_date || "暂无"}，已追踪 ${overview.tracked_days || 0} 天。`

  refs.heroKpis.innerHTML = heroKpis
    .map((item) => `
      <div class="hero-kpi">
        <span>${escapeHtml(item.label)}</span>
        <strong>${escapeHtml(item.value)}</strong>
        <small>${escapeHtml(item.detail)}</small>
      </div>
    `)
    .join("")

  const meta = [
    ["档案", profile.id || state.selectedProfile],
    ["快照状态", `${overview.snapshot_ok_count || 0}/${overview.snapshot_total_count || 0}`],
    ["活动覆盖", coverage.activity?.count ? `${coverage.activity.count} 天` : "暂无"],
    ["设备数", profile.device_count || 0],
    ["睡眠目标", profile.sleep_goal_minutes ? `${profile.sleep_goal_minutes} 分钟` : "未缓存"],
    ["步数目标", profile.daily_steps_goal ? `${formatNumber(profile.daily_steps_goal)} 步` : "未缓存"],
    ["会员起始", profile.member_since || "未知"],
    ["徽章数", profile.badge_count || 0],
  ]

  refs.heroMeta.innerHTML = meta
    .map(([label, value]) => `<span class="hero-meta-pill">${escapeHtml(label)} · ${escapeHtml(String(value))}</span>`)
    .join("")

  refs.heroRecoveryScore.textContent = overview.recovery_score ?? "--"
  refs.heroRecoveryLabel.textContent = overview.recovery_label || "等待数据"
  refs.heroRecoveryFootnote.textContent =
    `最近快照：${formatDateTime(snapshotStatus.saved_at || overview.latest_sync_at)}。scope：${missingScopes.length ? `${missingScopes.length} 项缺口` : "已覆盖"}。`
  refs.lastSyncText.textContent = formatDateTime(snapshotStatus.saved_at || overview.latest_sync_at)
}

function renderQuickNav() {
  refs.quickNavGrid.innerHTML = state.viewModel.quickNav
    .map((item) => `
      <button
        class="nav-card ${item.view === state.activeView ? "active" : ""}"
        type="button"
        data-jump-view="${escapeHtml(item.view)}"
        data-tone="${escapeHtml(item.tone || "blue")}"
        aria-pressed="${item.view === state.activeView ? "true" : "false"}"
      >
        <div class="nav-card-top">
          <span class="nav-card-kicker">${escapeHtml(item.kicker)}</span>
          <span class="nav-card-state">${item.view === state.activeView ? "当前" : "进入"}</span>
        </div>
        <strong>${escapeHtml(item.label)}</strong>
        <div class="nav-card-status">${escapeHtml(item.summary)}</div>
        <p>${escapeHtml(item.detail)}</p>
      </button>
    `)
    .join("")
}

function renderHealthDigest() {
  refs.healthDigestGrid.innerHTML = state.viewModel.guideCards
    .map((item) => `
      <button
        class="surface digest-card ${item.targetView === state.activeView ? "active" : ""}"
        type="button"
        data-jump-view="${escapeHtml(item.targetView)}"
        data-tone="${escapeHtml(item.tone || "blue")}"
        aria-pressed="${item.targetView === state.activeView ? "true" : "false"}"
      >
        <div class="digest-top">
          <div>
            <div class="section-kicker">${escapeHtml(item.kicker)}</div>
            <h3>${escapeHtml(item.label)}</h3>
          </div>
          <span class="digest-badge">${escapeHtml(item.targetView === state.activeView ? "当前章节" : item.badge)}</span>
        </div>
        <div class="digest-value">${escapeHtml(item.value)}</div>
        <div class="digest-meta">${escapeHtml(item.detail)}</div>
      </button>
    `)
    .join("")
}

function renderActiveViewSummary() {
  if (!refs.activeViewSummary || !state.viewModel) return
  const summary = buildActiveViewSummary(state.activeView)
  refs.activeViewSummary.innerHTML = `
    <div class="view-summary-head">
      <div>
        <div class="section-kicker">${escapeHtml(summary.kicker)}</div>
        <h3>${escapeHtml(summary.label)}</h3>
        <p>${escapeHtml(summary.description)}</p>
      </div>
      <div class="view-summary-state" data-tone="${escapeHtml(summary.tone || "blue")}">
        ${escapeHtml(summary.state)}
      </div>
    </div>
    <div class="summary-rail">
      ${summary.chips.map((item) => `
        <article class="summary-chip">
          <span>${escapeHtml(item.label)}</span>
          <strong>${escapeHtml(item.value)}</strong>
        </article>
      `).join("")}
    </div>
  `
}

function renderCoverage() {
  refs.coverageGrid.innerHTML = state.viewModel.foundationCards
    .map(({ label, tone, value, detail }) => {
      return `
        <article class="coverage-card" data-tone="${tone}">
          <div class="coverage-label">${escapeHtml(label)}</div>
          <div class="coverage-value">${escapeHtml(String(value ?? "--"))}</div>
          <div class="coverage-meta">${escapeHtml(detail || "暂无说明")}</div>
        </article>
      `
    })
    .join("")
}

function renderStats() {
  refs.statsGrid.innerHTML = state.viewModel.stats
    .map((card) => {
      const tone = card.tone || "blue"
      const trend = card.trend || {}
      const trendDirection = trend.direction || "flat"
      return `
        <button class="metric-card metric-card-button" type="button" data-tone="${escapeHtml(tone)}" data-jump-view="${escapeHtml(card.targetView || "overview")}">
          <div class="metric-topline">
            <div>
              <div class="metric-label">${escapeHtml(card.label || "--")}</div>
              <div class="metric-value">${escapeHtml(card.latestText)}</div>
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
              <strong>${escapeHtml(card.avg7Text)}</strong>
            </div>
            <div>
              <span>近 30 天均值</span>
              <strong>${escapeHtml(card.avg30Text)}</strong>
            </div>
          </div>
        </button>
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
  renderDetailGrid(refs.overviewHighlightGrid, state.viewModel.highlights)
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
    { key: "last_eaten", label: "最近食用" },
  ], state.dashboard.tables?.foods || [], {
    calories: (value) => value == null ? "--" : `${formatNumber(value)} kcal`,
    last_eaten: (value) => formatDate(value),
  })
}

function renderAccountView() {
  const snapshotStatus = state.viewModel.snapshotStatus || {}
  const files = state.viewModel.account.files || {}
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

  refs.fileList.innerHTML = state.viewModel.account.cacheLayers
    .map((item) => {
      const pathText = files[item.key]
      const pathBlock = pathText ? `<code>${escapeHtml(pathText)}</code>` : ""
      return `
        <div class="file-item" data-tone="${escapeHtml(item.tone || "blue")}">
          <strong>${escapeHtml(item.label)}</strong>
          <small>${escapeHtml(item.detail)}</small>
          ${pathBlock}
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
      syncRoute({ profile }, { mode: "push" })
      await loadDashboard()
      activateView("overview", { historyMode: "replace", scroll: true })
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
      requireAdmin: true,
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
  if (!refs.existingProfilesList) return
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
            ${state.admin.authenticated ? `<button class="button button-secondary" type="button" data-action="authorize" data-profile="${escapeHtml(profile.name)}">授权</button>` : ""}
            ${state.admin.authenticated ? `<button class="button button-secondary" type="button" data-action="delete" data-profile="${escapeHtml(profile.name)}">删除</button>` : ""}
          </div>
        </article>
      `
    })
    .join("")
}

async function startAuthorization(profileName) {
  const payload = await apiRequest(`/api/authorize/${encodeURIComponent(profileName)}`, {
    requireAdmin: true,
  })
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
      requireAdmin: true,
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
    requireAdmin: true,
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
    requireAdmin: true,
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
    requireAdmin: true,
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
      const payload = await apiRequest(`/api/fetch-status/${encodeURIComponent(state.fetchJobId)}`, {
        ignore404: true,
        requireAdmin: true,
      })
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

function activateView(viewName, { updateHistory = true, historyMode = "replace", scroll = false } = {}) {
  state.activeView = sanitizeView(viewName)
  document.querySelectorAll(".tab-button").forEach((button) => {
    const isActive = button.dataset.view === state.activeView
    button.classList.toggle("active", isActive)
    button.setAttribute("aria-selected", isActive ? "true" : "false")
    button.tabIndex = isActive ? 0 : -1
  })
  document.querySelectorAll("[data-view-panel]").forEach((panel) => {
    panel.classList.toggle("active", panel.dataset.viewPanel === state.activeView)
    panel.setAttribute("aria-hidden", panel.dataset.viewPanel === state.activeView ? "false" : "true")
  })
  if (state.viewModel) {
    renderQuickNav()
    renderHealthDigest()
    renderActiveViewSummary()
  }
  if (updateHistory) {
    syncRoute({ view: state.activeView }, { mode: historyMode })
  }
  scheduleActiveViewRender()
  if (scroll) {
    window.requestAnimationFrame(() => {
      ;(refs.activeViewSummary || document.querySelector(`[data-view-panel="${state.activeView}"]`))?.scrollIntoView({
        behavior: prefersReducedMotion() ? "auto" : "smooth",
        block: "start",
      })
    })
  }
}

function handleTabKeydown(event) {
  const keys = ["ArrowLeft", "ArrowRight", "Home", "End"]
  if (!keys.includes(event.key)) return

  const buttons = Array.from(document.querySelectorAll(".tab-button"))
  const currentIndex = buttons.indexOf(event.currentTarget)
  if (currentIndex === -1 || !buttons.length) return

  event.preventDefault()

  let nextIndex = currentIndex
  if (event.key === "ArrowRight") nextIndex = (currentIndex + 1) % buttons.length
  if (event.key === "ArrowLeft") nextIndex = (currentIndex - 1 + buttons.length) % buttons.length
  if (event.key === "Home") nextIndex = 0
  if (event.key === "End") nextIndex = buttons.length - 1

  const nextButton = buttons[nextIndex]
  if (!nextButton) return
  nextButton.focus()
  activateView(nextButton.dataset.view, { historyMode: "push", scroll: false })
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
  const windowSize = Number(refs.rangeSelect.value || DEFAULT_RANGE)
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
  const pointCount = getChartPointCount(config.data)
  const shouldAnimate = !prefersReducedMotion() && pointCount <= 60
  toggleChartEmptyState(canvas, hasData ? null : "data")
  if (!hasData) {
    clearCanvas(canvas)
    return
  }

  config.options = {
    responsive: true,
    maintainAspectRatio: false,
    normalized: true,
    devicePixelRatio: Math.min(window.devicePixelRatio || 1, 2),
    animation: shouldAnimate
      ? {
          duration: 420,
          easing: "easeOutCubic",
        }
      : false,
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
      decimation: config.type === "line" && pointCount > 48
        ? {
            enabled: true,
            algorithm: "lttb",
            samples: Math.min(48, pointCount),
          }
        : {
            enabled: false,
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

function getChartPointCount(data) {
  const datasets = Array.isArray(data?.datasets) ? data.datasets : []
  return datasets.reduce((max, dataset) => {
    const count = Array.isArray(dataset?.data) ? dataset.data.length : 0
    return Math.max(max, count)
  }, 0)
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

function queueVisibleChartResize() {
  if (queueVisibleChartResize.timeoutId) {
    window.clearTimeout(queueVisibleChartResize.timeoutId)
  }
  queueVisibleChartResize.timeoutId = window.setTimeout(() => {
    resizeVisibleCharts()
  }, 120)
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

async function applyRouteState() {
  const route = getRouteState()
  const nextView = sanitizeView(route.view)
  const nextRange = sanitizeRange(route.range)

  if (refs.rangeSelect && refs.rangeSelect.value !== nextRange) {
    refs.rangeSelect.value = nextRange
  }

  if (
    route.profile &&
    route.profile !== state.selectedProfile &&
    state.profiles.some((item) => item.name === route.profile)
  ) {
    state.selectedProfile = route.profile
    if (refs.profileSelect) refs.profileSelect.value = route.profile
    await loadDashboard()
    await loadProfileSummaries()
  }

  activateView(nextView, { updateHistory: false, scroll: false })
  if (state.dashboard) {
    renderDashboard()
  }
}

function normalizeDashboard(dashboard) {
  const profile = dashboard?.profile || {}
  const overview = dashboard?.overview || {}
  const coverage = dashboard?.coverage || {}
  const snapshotStatus = dashboard?.snapshot_status || {}
  const sections = dashboard?.sections || {}
  const stats = Array.isArray(dashboard?.stats) ? dashboard.stats : []
  const statsByKey = Object.fromEntries(stats.map((card) => [card.key, card]))
  const activityMetrics = sections.activity?.metrics || []
  const bodyMetrics = sections.body?.metrics || []
  const vitalsMetrics = sections.vitals?.metrics || []
  const lifestyleMetrics = sections.lifestyle?.metrics || []
  const accountMetrics = sections.account?.metrics || []
  const selectedRange = sanitizeRange(refs.rangeSelect?.value)
  const trackedDays = Number(overview.tracked_days || coverage.daily?.count || 0)
  const missingScopes = snapshotStatus.missing_scopes || []
  const activityLogsCount = Array.isArray(dashboard?.tables?.activity_logs) ? dashboard.tables.activity_logs.length : 0
  const foodsCount = Array.isArray(dashboard?.tables?.foods) ? dashboard.tables.foods.length : 0
  const vitalsCount = vitalsMetrics.filter((item) => item?.value != null).length

  const normalizedStats = stats.map((card) => ({
    ...card,
    targetView: METRIC_VIEW_MAP[card.key] || "overview",
    latestText: formatMetricValue(card.key, card.latest, card.unit),
    avg7Text: formatMetricValue(card.key, card.avg7, card.unit),
    avg30Text: formatMetricValue(card.key, card.avg30, card.unit),
  }))

  const heroKpis = [
    {
      label: "当前范围",
      value: `最近 ${selectedRange} 天`,
      detail: "图表和趋势会随范围切换同步刷新",
    },
    {
      label: "睡眠得分",
      value: formatMetricValue("sleep_score", statsByKey.sleep_score?.latest, statsByKey.sleep_score?.unit),
      detail: `近 7 天均值 ${formatMetricValue("sleep_score", statsByKey.sleep_score?.avg7, statsByKey.sleep_score?.unit)}`,
    },
    {
      label: "活动",
      value: formatMetricValue("steps", statsByKey.steps?.latest, statsByKey.steps?.unit),
      detail: `活跃分钟 ${formatMetricValue("active_minutes", statsByKey.active_minutes?.latest, statsByKey.active_minutes?.unit)}`,
    },
    {
      label: "覆盖",
      value: `${trackedDays} 天`,
      detail: `快照 ${overview.snapshot_ok_count || 0}/${overview.snapshot_total_count || 0}`,
    },
  ]

  const coverageCards = [
    {
      label: "活动历史",
      tone: "green",
      value: `${coverage.activity?.count || 0} 天`,
      detail: `${coverage.activity?.start_date || "暂无"} 至 ${coverage.activity?.end_date || "暂无"}`,
    },
    {
      label: "睡眠历史",
      tone: "amber",
      value: `${coverage.sleep?.count || 0} 天`,
      detail: `${coverage.sleep?.start_date || "暂无"} 至 ${coverage.sleep?.end_date || "暂无"}`,
    },
    {
      label: "恢复历史",
      tone: "teal",
      value: `${coverage.daily?.count || 0} 天`,
      detail: `HRV ${coverage.hrv?.count || 0} 天 · RHR ${coverage.rhr?.count || 0} 天`,
    },
    {
      label: "Fitbit 快照",
      tone: "blue",
      value: `${overview.snapshot_ok_count || 0}/${overview.snapshot_total_count || 0}`,
      detail: `最近同步 ${formatDateTime(snapshotStatus.saved_at || overview.latest_sync_at)}`,
    },
    {
      label: "活动补充",
      tone: "amber",
      value: `${activityLogsCount} 条`,
      detail: "今日摘要、活动日志和 lifetime stats 已并入缓存。",
    },
    {
      label: "权限缺口",
      tone: "red",
      value: `${missingScopes.length} 项`,
      detail: missingScopes.length ? missingScopes.join(" / ") : "当前页面所需 scope 已覆盖。",
    },
  ]

  const highlights = [
    ...(activityMetrics || []).slice(0, 2),
    ...(bodyMetrics || []).slice(0, 2),
    ...(vitalsMetrics || []).slice(0, 1),
    ...(lifestyleMetrics || []).slice(0, 1),
    ...(accountMetrics || []).slice(0, 1),
  ]

  const guideCards = [
    {
      kicker: "Step 1",
      label: "先确认整体状态",
      badge: "总览",
      value: `恢复 ${overview.recovery_score ?? "--"}`,
      detail: `${overview.recovery_label || "等待恢复判断"} · 快照 ${overview.snapshot_ok_count || 0}/${overview.snapshot_total_count || 0}`,
      targetView: "overview",
      tone: "blue",
    },
    {
      kicker: "Step 2",
      label: "再看三条主线",
      badge: "睡眠 / 活动 / 恢复",
      value: `${formatMetricValue("sleep_score", statsByKey.sleep_score?.latest, statsByKey.sleep_score?.unit)} · ${formatMetricValue("steps", statsByKey.steps?.latest, statsByKey.steps?.unit)} · ${formatMetricValue("hrv", statsByKey.hrv?.latest, statsByKey.hrv?.unit)}`,
      detail: "用睡眠、活动、恢复三条主线解释当天状态。",
      targetView: "sleep",
      tone: "green",
    },
    {
      kicker: "Step 3",
      label: "最后补充背景",
      badge: "体征 / 生活 / 账户",
      value: `${vitalsCount} 项体征 · ${foodsCount} 条食物`,
      detail: `把体重、饮水、设备和权限状态放到最后补充，避免一开始过载。`,
      targetView: "body",
      tone: "amber",
    },
  ]

  const quickNav = VIEW_ORDER.map((view) => {
    const meta = VIEW_META[view]
    return {
      view,
      label: meta.label,
      kicker: meta.kicker,
      tone: meta.tone,
      summary: buildViewSummary(view, { overview, snapshotStatus, statsByKey, activityMetrics, bodyMetrics, lifestyleMetrics }),
      detail: buildViewDetail(view, { overview, snapshotStatus, activityMetrics, bodyMetrics, vitalsMetrics, lifestyleMetrics }),
    }
  })

  const cacheLayers = buildCacheLayers()
  const files = state.admin.authenticated ? dashboard?.files || {} : {}

  return {
    profile,
    overview,
    coverage,
    snapshotStatus,
    stats: normalizedStats,
    statsByKey,
    heroKpis,
    foundationCards: coverageCards,
    guideCards,
    quickNav,
    highlights,
    account: {
      files,
      cacheLayers,
    },
  }
}

function buildViewSummary(view, context) {
  const { overview, snapshotStatus, statsByKey, bodyMetrics, lifestyleMetrics, activityMetrics } = context
  if (view === "overview") {
    return `恢复 ${overview.recovery_score ?? "--"} · 快照 ${overview.snapshot_ok_count || 0}/${overview.snapshot_total_count || 0}`
  }
  if (view === "sleep") {
    return `${formatMetricValue("sleep_score", statsByKey.sleep_score?.latest, statsByKey.sleep_score?.unit)} · ${formatMetricValue("sleep_hours", statsByKey.sleep_hours?.latest, statsByKey.sleep_hours?.unit)}`
  }
  if (view === "activity") {
    return `${formatMetricValue("steps", statsByKey.steps?.latest, statsByKey.steps?.unit)} · ${activityMetrics[3]?.label || "活动日志"} ${formatDetailValue(activityMetrics[3]?.value)}${activityMetrics[3]?.unit ? ` ${activityMetrics[3].unit}` : ""}`
  }
  if (view === "recovery") {
    return `${formatMetricValue("hrv", statsByKey.hrv?.latest, statsByKey.hrv?.unit)} · ${formatMetricValue("rhr", statsByKey.rhr?.latest, statsByKey.rhr?.unit)}`
  }
  if (view === "body") {
    const first = bodyMetrics[0]
    const second = bodyMetrics[1]
    return `${first?.label || "体征摘要"} ${formatDetailValue(first?.value)}${first?.unit || ""} · ${second?.label || "BMI"}`
  }
  if (view === "lifestyle") {
    const intake = lifestyleMetrics.find((item) => item.label === "今日摄入")
    const water = lifestyleMetrics.find((item) => item.label === "今日饮水")
    return `${intake?.label || "饮食"} ${formatDetailValue(intake?.value)}${intake?.unit || ""} · ${water?.label || "饮水"} ${formatDetailValue(water?.value)}${water?.unit || ""}`
  }
  if (view === "account") {
    return `设备 ${context.overview.device_count || 0} 台 · 缺失 scope ${snapshotStatus.missing_scopes?.length || 0} 项`
  }
  if (view === "family") {
    return `档案 ${state.profileSummaries.length || state.profiles.length || 0} 个 · 统一切换和对比`
  }
  return VIEW_META[view]?.summary || ""
}

function buildViewDetail(view, context) {
  const { activityMetrics, bodyMetrics, vitalsMetrics, lifestyleMetrics } = context
  if (view === "overview") return "先看恢复、覆盖和补充数据，再决定深入哪一页。"
  if (view === "sleep") return "主看睡眠得分、时长和阶段结构。"
  if (view === "activity") return `除了步数和活跃分钟，还缓存了 ${activityMetrics[3]?.value ?? 0}${activityMetrics[3]?.unit ? ` ${activityMetrics[3].unit}` : ""} Fitbit 活动日志。`
  if (view === "recovery") return "主看 HRV、静息心率，以及它们和睡眠的联动。"
  if (view === "body") return `${bodyMetrics[0]?.label || "体重"}、${bodyMetrics[1]?.label || "BMI"} 和 ${vitalsMetrics.length || 0} 项体征放在一起看。`
  if (view === "lifestyle") return `饮食、饮水和 ${lifestyleMetrics.slice(4).length} 类 nutrition 缓存集中查看。`
  if (view === "account") return "设备、scope、缓存层和接口状态集中收口。"
  if (view === "family") return "所有档案只保留一个统一对比入口。"
  return VIEW_META[view]?.summary || ""
}

function buildActiveViewSummary(view) {
  const meta = VIEW_META[view] || VIEW_META.overview
  const { overview, snapshotStatus, statsByKey } = state.viewModel
  const rangeText = `最近 ${sanitizeRange(refs.rangeSelect?.value)} 天`
  const latestSync = formatDateTime(snapshotStatus.saved_at || overview.latest_sync_at)
  const foodsCount = Array.isArray(state.dashboard?.tables?.foods) ? state.dashboard.tables.foods.length : 0
  const activityLogsCount = Array.isArray(state.dashboard?.tables?.activity_logs) ? state.dashboard.tables.activity_logs.length : 0
  const devicesCount = Array.isArray(state.dashboard?.tables?.devices) ? state.dashboard.tables.devices.length : 0
  const activityMetrics = state.dashboard?.sections?.activity?.metrics || []
  const bodyMetrics = state.dashboard?.sections?.body?.metrics || []
  const vitalsMetrics = state.dashboard?.sections?.vitals?.metrics || []
  const lifestyleMetrics = state.dashboard?.sections?.lifestyle?.metrics || []
  const activeProfile = state.viewModel.profile?.display_name || state.selectedProfile || "当前档案"

  const summary = {
    kicker: meta.kicker,
    label: meta.label,
    tone: meta.tone,
    state: `${activeProfile} · ${rangeText}`,
    description: meta.summary,
    chips: [
      { label: "档案", value: activeProfile },
      { label: "范围", value: rangeText },
      { label: "最近同步", value: latestSync },
    ],
  }

  if (view === "overview") {
    summary.description = "先看整体趋势、信号判断和补充快照，再决定深入哪一页。"
    summary.chips = [
      { label: "恢复指数", value: overview.recovery_score ?? "--" },
      { label: "最近记录", value: overview.latest_date || "暂无" },
      { label: "快照完成", value: `${overview.snapshot_ok_count || 0}/${overview.snapshot_total_count || 0}` },
    ]
    return summary
  }

  if (view === "sleep") {
    summary.description = "睡眠页优先看得分、时长和阶段结构，判断恢复底盘是否稳定。"
    summary.chips = [
      { label: "睡眠得分", value: formatMetricValue("sleep_score", statsByKey.sleep_score?.latest, statsByKey.sleep_score?.unit) },
      { label: "睡眠时长", value: formatMetricValue("sleep_hours", statsByKey.sleep_hours?.latest, statsByKey.sleep_hours?.unit) },
      { label: "睡眠目标", value: goalText(statsByKey.sleep_hours || {}) },
    ]
    return summary
  }

  if (view === "activity") {
    summary.description = "活动页先看日趋势，再看 Fitbit 原始活动日志和补充活动摘要。"
    summary.chips = [
      { label: "步数", value: formatMetricValue("steps", statsByKey.steps?.latest, statsByKey.steps?.unit) },
      { label: "活跃分钟", value: formatMetricValue("active_minutes", statsByKey.active_minutes?.latest, statsByKey.active_minutes?.unit) },
      { label: activityMetrics[3]?.label || "活动日志", value: `${formatDetailValue(activityMetrics[3]?.value)}${activityMetrics[3]?.unit ? ` ${activityMetrics[3].unit}` : ""}`.trim() || `${activityLogsCount} 条` },
    ]
    return summary
  }

  if (view === "recovery") {
    summary.description = "恢复页主看 HRV 和静息心率，再结合睡眠得分判断近期波动是否异常。"
    summary.chips = [
      { label: "HRV", value: formatMetricValue("hrv", statsByKey.hrv?.latest, statsByKey.hrv?.unit) },
      { label: "静息心率", value: formatMetricValue("rhr", statsByKey.rhr?.latest, statsByKey.rhr?.unit) },
      { label: "当前判断", value: overview.recovery_label || "等待恢复判断" },
    ]
    return summary
  }

  if (view === "body") {
    summary.description = "体征页把体重目标、BMI、体脂和 vitals 数据合并成一个更整洁的身体状态视图。"
    summary.chips = [
      { label: bodyMetrics[0]?.label || "体重", value: `${formatDetailValue(bodyMetrics[0]?.value)}${bodyMetrics[0]?.unit ? ` ${bodyMetrics[0].unit}` : ""}`.trim() },
      { label: bodyMetrics[1]?.label || "BMI", value: `${formatDetailValue(bodyMetrics[1]?.value)}${bodyMetrics[1]?.unit ? ` ${bodyMetrics[1].unit}` : ""}`.trim() },
      { label: "补充体征", value: vitalsMetrics.length ? `${vitalsMetrics.length} 项` : "暂无" },
    ]
    return summary
  }

  if (view === "lifestyle") {
    summary.description = "生活页集中展示饮食和饮水，不让 nutrition 数据散在账户或表格深处。"
    summary.chips = [
      { label: lifestyleMetrics[0]?.label || "今日摄入", value: `${formatDetailValue(lifestyleMetrics[0]?.value)}${lifestyleMetrics[0]?.unit ? ` ${lifestyleMetrics[0].unit}` : ""}`.trim() },
      { label: lifestyleMetrics[1]?.label || "饮水目标", value: `${formatDetailValue(lifestyleMetrics[1]?.value)}${lifestyleMetrics[1]?.unit ? ` ${lifestyleMetrics[1].unit}` : ""}`.trim() },
      { label: "近期食物", value: foodsCount ? `${foodsCount} 条` : "暂无" },
    ]
    return summary
  }

  if (view === "account") {
    summary.description = "账户页聚合设备、权限和缓存状态，公开态看结构，管理员态再看具体路径。"
    summary.chips = [
      { label: "设备", value: devicesCount ? `${devicesCount} 台` : "暂无" },
      { label: "缺失 Scope", value: `${snapshotStatus.missing_scopes?.length || 0} 项` },
      { label: "缓存层", value: `${state.viewModel.account.cacheLayers.length} 层` },
    ]
    return summary
  }

  if (view === "family") {
    summary.description = "多档案页只保留一个横向对比入口，切换档案不再依赖旧式 spousal 页面。"
    summary.chips = [
      { label: "档案数", value: `${state.profileSummaries.length || state.profiles.length || 0} 个` },
      { label: "当前档案", value: activeProfile },
      { label: "最近同步", value: latestSync },
    ]
    return summary
  }

  return summary
}

function buildCacheLayers() {
  const layers = [
    { key: "dashboard_cache", label: "统一 dashboard 缓存", detail: "页面主入口，聚合 overview / stats / charts / tables。", tone: "blue" },
    { key: "profile_snapshot", label: "Fitbit 快照缓存", detail: "补充 profile、goals、vitals、devices 和 nutrition 元数据。", tone: "teal" },
    { key: "activity_csv", label: "活动历史缓存", detail: "原始活动序列，支撑活动页和趋势图。", tone: "green" },
    { key: "sleep_csv", label: "睡眠历史缓存", detail: "原始睡眠记录，支撑睡眠页和恢复估算。", tone: "amber" },
    { key: "hrv_csv", label: "HRV 历史缓存", detail: "恢复趋势与散点相关性分析来源。", tone: "blue" },
    { key: "rhr_csv", label: "静息心率缓存", detail: "恢复视图的第二核心指标。", tone: "red" },
  ]
  if (state.admin.authenticated) {
    layers.push(
      { key: "tokens", label: "授权 token 文件", detail: "仅管理员可见，用于 Fitbit OAuth 刷新。", tone: "red" },
      { key: "client", label: "Client 凭据文件", detail: "仅管理员可见，用于管理档案授权。", tone: "red" },
    )
  }
  return layers
}

function getRouteState() {
  const params = new URLSearchParams(window.location.search)
  return {
    profile: params.get("profile"),
    view: params.get("view"),
    range: params.get("range"),
  }
}

function syncRoute(values, { mode = "replace" } = {}) {
  const params = new URLSearchParams(window.location.search)
  Object.entries(values).forEach(([key, value]) => {
    if (value == null || value === "") {
      params.delete(key)
    } else {
      params.set(key, value)
    }
  })
  const next = `${window.location.pathname}?${params.toString()}`
  const url = next.endsWith("?") ? window.location.pathname : next
  if (mode === "push") {
    window.history.pushState({}, "", url)
    return
  }
  window.history.replaceState({}, "", url)
}

function sanitizeView(viewName) {
  return VIEW_ORDER.includes(viewName) ? viewName : "overview"
}

function sanitizeRange(value) {
  return VALID_RANGES.has(String(value || "")) ? String(value) : DEFAULT_RANGE
}

function prefersReducedMotion() {
  return window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches ?? false
}

async function apiRequest(path, options = {}) {
  const { ignore404, requireAdmin, skipAdminHandling, ...fetchOptions } = options
  const headers = new Headers(fetchOptions.headers || {})
  if (requireAdmin && state.admin.csrfToken) {
    headers.set("X-FitBaus-CSRF", state.admin.csrfToken)
  }

  const response = await fetch(path, {
    credentials: "same-origin",
    ...fetchOptions,
    headers,
  })

  if (ignore404 && response.status === 404) {
    return null
  }

  const contentType = response.headers.get("content-type") || ""
  const payload = contentType.includes("application/json") ? await response.json() : await response.text()
  if (!response.ok) {
    if (requireAdmin && !skipAdminHandling && [401, 403, 503].includes(response.status) && typeof payload !== "string") {
      applyAdminSession(payload)
    }
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
  const delta = formatNumber(trend.delta, metricValueDigits(card.key))
  const percent = trend.percent == null ? "" : ` (${sign}${formatNumber(trend.percent, 1)}%)`
  return `${sign}${delta}${metricUnitSuffix(card.key, card.unit)}${percent}`
}

function formatMetricValue(key, value, unit) {
  if (value == null) return "--"
  return `${formatNumber(value, metricValueDigits(key))}${metricUnitSuffix(key, unit)}`
}

function metricValueDigits(key) {
  return ["sleep_hours", "sleep_score", "hrv"].includes(key) ? 1 : 0
}

function metricUnitSuffix(key, unit) {
  const map = {
    sleep_hours: " 小时",
    sleep_score: " 分",
    hrv: " ms",
    rhr: " bpm",
    calories_out: " kcal",
    steps: " 步",
    active_minutes: " 分钟",
    active_zone_minutes: " 分钟",
  }
  if (map[key]) return map[key]
  return unit ? ` ${unit}` : ""
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
