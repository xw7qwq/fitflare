import { escapeHtml, formatDate, formatDateTime, formatNumber, statusLabel } from './js/format.js';
import { VIEWS, normalizeDashboard, selectDateWindow } from './js/data.js';
import { destroyAllCharts, resizeVisibleCharts } from './js/charts.js';
import { createViews } from './js/views.js';
import { createTableRenderer } from './js/table.js';

const state = {
  profiles: [], selectedProfile: null, dashboard: null, viewModel: null, profileSummaries: [],
  admin: { configured: false, authenticated: false, csrfToken: null },
  activeView: 'overview', authProfile: null, fetchJobId: null, fetchTimer: null, fetchNotFoundCount: 0,
};
const refs = Object.fromEntries([...document.querySelectorAll('[id]')].map(element => [element.id, element]));
const renderTable = createTableRenderer({ apiRequest, getContext: () => ({ profile: state.selectedProfile, version: state.dashboard?.generated_at, authenticated: state.admin.authenticated }) });
const views = createViews({ state, refs, getDailySeries, renderTable });
const VALID_RANGES = new Set(['14', '30', '90']);
let dashboardRequest = 0;
let dashboardAbort;
let summariesRequest = null;
let summariesLoaded = false;
let summariesEpoch = 0;
let summariesAbort;

async function initializeApp() {
  bindEvents();
  const route = getRouteState();
  refs.rangeSelect.value = sanitizeRange(route.range);
  activateView(route.view, { updateHistory: false });
  await refreshAdminSession({ silent: true });
  await refreshProfiles();
}
function bindEvents() {
  refs.profileSelect.addEventListener('change', () => selectProfile(refs.profileSelect.value).catch(handleAsyncError));
  refs.rangeSelect.addEventListener('change', () => {
    syncRoute({ range: refs.rangeSelect.value }, { mode: 'push' });
    renderDashboard();
  });
  const actions = {
    syncBtn: startFetch, reloadBtn: rebuildDashboard, retryBtn: refreshProfiles,
    adminLoginBtn: openAdminModal, adminLogoutBtn: logoutAdmin,
    openManagerBtn: () => { renderExistingProfilesList(); openModal('profileModal'); },
  };
  for (const [id, action] of Object.entries(actions)) {
    refs[id].addEventListener('click', () => Promise.resolve().then(action).catch(handleAsyncError));
  }
  for (const [id, action] of Object.entries({ adminPasswordForm: loginAdmin, createProfileForm: createProfile, authExchangeForm: submitAuthorization })) {
    refs[id].addEventListener('submit', event => { event.preventDefault(); action().catch(handleAsyncError); });
  }
  document.querySelectorAll('.tab-button').forEach(button => {
    button.addEventListener('click', () => activateView(button.dataset.view, { historyMode: 'push' }));
    button.addEventListener('keydown', handleTabKeydown);
  });
  document.addEventListener('click', event => {
    const target = event.target.closest('button');
    if (!target) return;
    if (target.dataset.closeModal) closeModal(target.dataset.closeModal);
    if (target.dataset.retrySummaries) loadProfileSummaries().catch(handleAsyncError);
    if (target.dataset.jumpView) activateView(target.dataset.jumpView, { historyMode: 'push' });
    if (target.dataset.jumpProfile) {
      activateView('overview', { updateHistory: false });
      selectProfile(target.dataset.jumpProfile).catch(handleAsyncError);
    }
  });
  refs.existingProfilesList.addEventListener('click', event => {
    const target = event.target.closest('button[data-action]');
    const profile = target?.dataset.profile;
    if (!profile) return;
    const actions = {
      open: async () => { closeModal('profileModal'); await selectProfile(profile); },
      authorize: () => startAuthorization(profile), delete: () => deleteProfile(profile),
    };
    Promise.resolve().then(actions[target.dataset.action]).catch(handleAsyncError);
  });
  document.querySelectorAll('dialog').forEach(dialog => {
    dialog.addEventListener('click', event => { if (event.target === dialog) dialog.close(); });
    dialog.addEventListener('close', () => dialog.querySelectorAll('input[type=password], textarea').forEach(input => { input.value = ''; }));
  });
  window.addEventListener('popstate', () => applyRouteState().catch(handleAsyncError));
}
async function refreshProfiles(preferredProfile) {
  summariesAbort?.abort();
  summariesRequest = null;
  summariesLoaded = false;
  summariesEpoch++;
  state.profileSummaries = [];
  let profiles;
  try { profiles = await apiRequest('/api/profiles'); }
  catch (error) {
    if (error.code !== 'private_data') throw error;
    state.profiles = [];
    state.selectedProfile = null;
    state.dashboard = null;
    state.viewModel = null;
    populateProfileSelect();
    renderEmptyState('此站点为私有模式，请使用顶部的管理员登录查看数据。');
    setDashboardLoading(false);
    setStatus('等待登录');
    return;
  }
  state.profiles = Array.isArray(profiles) ? profiles : [];
  populateProfileSelect(preferredProfile);
  renderExistingProfilesList();
  await loadDashboard();
  if (state.activeView === 'family') await loadProfileSummaries();
}
function populateProfileSelect(preferredProfile) {
  const requested = preferredProfile || getRouteState().profile || state.selectedProfile;
  state.selectedProfile = state.profiles.find(profile => profile.name === requested)?.name || state.profiles[0]?.name || null;
  refs.profileSelect.innerHTML = state.profiles.length
    ? state.profiles.map(profile => `<option value="${escapeHtml(profile.name)}">${escapeHtml(profile.name)}</option>`).join('')
    : '<option value="">暂无档案</option>';
  refs.profileSelect.disabled = !state.profiles.length;
  refs.profileSelect.value = state.selectedProfile || '';
  syncRoute({ profile: state.selectedProfile });
}
async function selectProfile(profile, updateHistory = true) {
  if (!state.profiles.some(item => item.name === profile)) return;
  state.selectedProfile = profile;
  refs.profileSelect.value = profile;
  if (updateHistory) syncRoute({ profile, view: state.activeView }, { mode: 'push' });
  await loadDashboard();
}
async function loadDashboard() {
  const requestId = ++dashboardRequest;
  dashboardAbort?.abort();
  dashboardAbort = new AbortController();
  state.dashboard = null;
  state.viewModel = null;
  setDashboardLoading(true);
  renderEmptyState(state.selectedProfile ? '正在读取本地缓存…' : '暂无档案。管理员登录后可创建并授权 Fitbit 档案。');
  if (!state.selectedProfile) { setStatus('等待档案'); setDashboardLoading(false); return; }
  try {
    setStatus('正在读取缓存');
    const payload = await apiRequest(`/api/dashboard/${encodeURIComponent(state.selectedProfile)}?tables=none`, { signal: dashboardAbort.signal });
    if (requestId !== dashboardRequest) return;
    state.dashboard = payload;
    state.viewModel = normalizeDashboard(payload, state.admin.authenticated);
    renderDashboard();
    setStatus('已载入本地缓存');
  } catch (error) {
    if (error.name === 'AbortError' || requestId !== dashboardRequest) return;
    renderEmptyState('读取失败。请检查连接后重试。', true);
    throw error;
  } finally {
    if (requestId === dashboardRequest) setDashboardLoading(false);
  }
}
async function loadProfileSummaries() {
  if (summariesLoaded) return;
  if (summariesRequest) return summariesRequest;
  const epoch = summariesEpoch;
  summariesAbort = new AbortController();
  const signal = summariesAbort.signal;
  refs.familyGrid.innerHTML = '<div class="empty-state">正在读取档案…</div>';
  summariesRequest = (async () => {
    try {
      const payload = await apiRequest('/api/profile-summaries', { signal });
      if (epoch !== summariesEpoch) return;
      state.profileSummaries = Array.isArray(payload) ? payload : [];
      summariesLoaded = true;
      if (state.activeView === 'family') views.family();
    } catch (error) {
      if (error.name === 'AbortError' || epoch !== summariesEpoch) return;
      if (epoch === summariesEpoch) refs.familyGrid.innerHTML = '<div class="empty-state">档案读取失败。<button class="button" type="button" data-retry-summaries="true">重试</button></div>';
      throw error;
    } finally { if (epoch === summariesEpoch) summariesRequest = null; }
  })();
  return summariesRequest;
}
function renderEmptyState(message = '暂无可显示的数据。', retry = false) {
  destroyAllCharts();
  refs.dashboardNotice.hidden = false;
  refs.dashboardNoticeText.textContent = message;
  refs.retryBtn.hidden = !retry;
  refs.overviewMetrics.hidden = true;
  refs.latestRecordText.textContent = '暂无记录';
  refs.lastSyncText.textContent = '暂无';
  document.querySelectorAll('[data-view-panel]').forEach(panel => { panel.hidden = true; });
}
function renderDashboard() {
  refs.pageTitle.textContent = VIEWS[state.activeView][0];
  refs.pageDescription.textContent = VIEWS[state.activeView][1];
  if (!state.dashboard) return;
  const { overview, snapshotStatus } = state.viewModel;
  refs.dashboardNotice.hidden = true;
  refs.latestRecordText.textContent = formatDate(overview.latest_date);
  refs.lastSyncText.textContent = formatDateTime(snapshotStatus.saved_at || overview.latest_sync_at);
  refs.overviewMetrics.hidden = state.activeView !== 'overview';
  document.querySelectorAll('[data-view-panel]').forEach(panel => { panel.hidden = panel.dataset.viewPanel !== state.activeView; });
  if (state.activeView === 'overview') renderStats();
  views[state.activeView]();
  if (state.activeView === 'family' && !summariesLoaded) loadProfileSummaries().catch(handleAsyncError);
  requestAnimationFrame(resizeVisibleCharts);
}
function renderStats() {
  const core = [['sleep_hours', '睡眠时长'], ['steps', '步数'], ['hrv', 'HRV'], ['rhr', '静息心率']];
  const cardHtml = card => `<button class="metric-card" type="button" data-jump-view="${escapeHtml(card.targetView || 'overview')}">
    <span class="metric-label">${escapeHtml(card.label)}</span>
    <strong class="metric-value">${escapeHtml(card.latestText || '--')}</strong>
    <span class="metric-meta">最新 ${escapeHtml(formatDate(card.latest_date, 'short'))}</span>
    <span class="metric-average">7 天均值 <b>${escapeHtml(card.avg7Text || '--')}</b></span>
  </button>`;
  refs.statsGrid.innerHTML = core.map(([key, label]) => cardHtml(state.viewModel.statsByKey[key] || { label })).join('');
  const others = state.viewModel.stats.filter(card => !core.some(([key]) => key === card.key));
  refs.moreStatsGrid.innerHTML = others.map(cardHtml).join('');
  refs.moreMetrics.hidden = !others.length;
}
function getDailySeries() { return selectDateWindow(state.dashboard?.charts?.daily || [], refs.rangeSelect.value); }
function getRouteState() { return Object.fromEntries(new URLSearchParams(window.location.search)); }
function sanitizeRange(value) { return VALID_RANGES.has(String(value)) ? String(value) : '30'; }
function syncRoute(values, { mode = 'replace' } = {}) {
  const url = new URL(window.location.href);
  for (const [key, value] of Object.entries(values)) value == null || value === '' ? url.searchParams.delete(key) : url.searchParams.set(key, value);
  if (url.href !== window.location.href) window.history[mode === 'push' ? 'pushState' : 'replaceState']({}, '', url);
}
function activateView(view, { updateHistory = true, historyMode = 'replace' } = {}) {
  state.activeView = Object.hasOwn(VIEWS, view) ? view : 'overview';
  document.querySelectorAll('.tab-button').forEach(button => {
    const active = button.dataset.view === state.activeView;
    button.setAttribute('aria-selected', String(active));
    button.tabIndex = active ? 0 : -1;
  });
  if (updateHistory) syncRoute({ view: state.activeView }, { mode: historyMode });
  renderDashboard();
}
function handleTabKeydown(event) {
  if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
  event.preventDefault();
  const tabs = [...document.querySelectorAll('.tab-button')];
  const offset = event.key === 'ArrowLeft' ? -1 : 1;
  const index = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : (tabs.indexOf(event.currentTarget) + offset + tabs.length) % tabs.length;
  tabs[index].focus();
  activateView(tabs[index].dataset.view, { historyMode: 'push' });
}
async function applyRouteState() {
  const route = getRouteState();
  refs.rangeSelect.value = sanitizeRange(route.range);
  activateView(route.view, { updateHistory: false });
  if (route.profile !== state.selectedProfile) await selectProfile(route.profile, false);
}
function openModal(id) { if (refs[id] && !refs[id].open) refs[id].showModal(); }
function closeModal(id) { if (refs[id]?.open) refs[id].close(); }

// Management and API handlers follow. Their endpoint and CSRF contracts are unchanged.

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
  if (state.dashboard) {
    state.viewModel = normalizeDashboard(state.dashboard, state.admin.authenticated)
    renderDashboard()
  }
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
    await refreshProfiles(state.selectedProfile)
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
  await refreshProfiles(state.selectedProfile)
  showToast("已退出管理员模式。")
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
  await refreshProfiles(state.selectedProfile)
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
          await refreshProfiles(state.selectedProfile)
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
    const message = typeof payload === "string" ? `请求失败 (${response.status})` : payload.error || payload.message || "请求失败"
    const error = new Error(typeof message === 'object' ? message.message || '请求失败' : message)
    error.status = response.status
    error.code = payload?.code || payload?.error?.code
    throw error
  }
  return payload
}

function setStatus(text) {
  refs.statusText.textContent = text || "等待数据"
}

function setDashboardLoading(isLoading) {
  document.body.classList.toggle("dashboard-loading", Boolean(isLoading))
  const mainContent = document.getElementById("mainContent")
  if (mainContent) {
    mainContent.setAttribute("aria-busy", isLoading ? "true" : "false")
  }
}

function setButtonState(button, disabled, label) {
  if (!button) return
  button.disabled = disabled
  if (label) button.textContent = label
}

function showToast(message, isError = false) {
  if (!refs.toast) return
  ;(document.querySelector('dialog[open]') || document.body).append(refs.toast)
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

initializeApp().catch(error => { setDashboardLoading(false); renderEmptyState("初始化失败，请刷新页面重试。", true); handleAsyncError(error); });
