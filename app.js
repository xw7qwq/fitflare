import { escapeHtml, formatDate, formatDateTime, statusLabel } from './js/format.js';
import { VIEWS, VIEW_ALIASES, normalizeDashboard } from './js/data.js';
import { destroyAllCharts, resizeVisibleCharts } from './js/charts.js';
import { createViews } from './js/views.js';
import { createTableRenderer } from './js/table.js';

const state = {
  account: { profile_id: null, configured: false, authorized: false, has_data: false }, dashboard: null, viewModel: null, revision: 0,
  admin: { configured: false, authenticated: false, csrfToken: null, private: true },
  activeView: 'overview', monthlyMetric: 'steps', authorizationOpen: false, fetchJobId: null, fetchTimer: null, fetchNotFoundCount: 0,
};
const refs = Object.fromEntries([...document.querySelectorAll('[id]')].map(element => [element.id, element]));
const renderTable = createTableRenderer({ apiRequest, getContext: () => ({ revision: state.revision, version: state.dashboard?.generated_at, authenticated: state.admin.authenticated }) });
const views = createViews({ state, refs, renderTable });
const VALID_RANGES = new Set(['14', '30', '90']);
let dashboardRequest = 0;
let dashboardAbort;
let accountRequest = 0;
let accountAbort;

async function initializeApp() {
  bindEvents();
  const route = getRouteState();
  refs.rangeSelect.value = sanitizeRange(route.range);
  activateView(route.view, { updateHistory: false });
  syncRoute({ view: state.activeView, range: refs.rangeSelect.value });
  await refreshAdminSession({ silent: true });
  await refreshAccount();
}
function bindEvents() {
  refs.rangeSelect.addEventListener('change', () => {
    syncRoute({ range: refs.rangeSelect.value }, { mode: 'push' });
    renderDashboard();
  });
  const actions = {
    syncBtn: startFetch, reloadBtn: rebuildDashboard, retryBtn: refreshAccount,
    adminLoginBtn: openAdminModal, adminLogoutBtn: logoutAdmin,
    openAccountBtn: () => { renderAccountSettings(); openModal('accountModal'); },
    reauthorizeBtn: startAuthorization,
  };
  for (const [id, action] of Object.entries(actions)) {
    refs[id].addEventListener('click', () => Promise.resolve().then(action).catch(handleAsyncError));
  }
  for (const [id, action] of Object.entries({ adminPasswordForm: loginAdmin, accountSetupForm: setupAccount, authExchangeForm: submitAuthorization })) {
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
    if (target.dataset.jumpView) activateView(target.dataset.jumpView, { historyMode: 'push' });
  });
  document.querySelectorAll('dialog').forEach(dialog => {
    dialog.addEventListener('click', event => { if (event.target === dialog) dialog.close(); });
    dialog.addEventListener('close', () => dialog.querySelectorAll('input[type=password], textarea').forEach(input => { input.value = ''; }));
  });
  window.addEventListener('popstate', () => applyRouteState().catch(handleAsyncError));
  // Chart colors come from theme tokens, so a system theme switch needs a redraw.
  window.matchMedia?.('(prefers-color-scheme: light)').addEventListener?.('change', () => { if (state.dashboard) { destroyAllCharts(); renderDashboard(); } });
}
function invalidateDashboard() {
  dashboardRequest++;
  dashboardAbort?.abort();
  state.revision++;
  state.dashboard = null;
  state.viewModel = null;
  destroyAllCharts();
  document.querySelectorAll('.table-wrap').forEach(container => { container.innerHTML = ''; });
}
async function refreshAccount() {
  const requestId = ++accountRequest;
  accountAbort?.abort();
  accountAbort = new AbortController();
  invalidateDashboard();
  setDashboardLoading(true);
  renderEmptyState('正在读取健康记录…');
  try {
    const payload = await apiRequest('/api/account', { signal: accountAbort.signal });
    if (requestId !== accountRequest) return;
    state.account = { profile_id: payload.profile_id || null, configured: Boolean(payload.configured), authorized: Boolean(payload.authorized), has_data: Boolean(payload.has_data), provider: payload.provider || null };
    renderAccountSettings();
    if (!state.account.has_data) {
      const notice = state.account.authorized ? 'Google Health 已连接。点击顶部“同步数据”，载入你的健康记录。' : state.account.configured ? 'Google 客户端已保存。请在账户设置中完成 Google Health 授权。' : '欢迎使用 Fitflare。点击顶部“连接 Google Health”，开始记录你的健康趋势。';
      renderEmptyState(state.admin.authenticated ? notice : '暂无健康记录，请登录后连接 Google Health 并同步数据。');
      setStatus(state.account.authorized ? '等待同步' : '等待连接 Google Health');
      setDashboardLoading(false);
      return;
    }
    await loadDashboard();
  } catch (error) {
    if (error.name === 'AbortError' || requestId !== accountRequest) return;
    if (error.code === 'private_data') {
      state.account = { profile_id: null, configured: false, authorized: false, has_data: false };
      renderAccountSettings();
      renderEmptyState('此站点为私有模式，请先登录查看自己的健康记录。');
      setStatus('等待登录');
    } else {
      renderEmptyState('读取失败，请检查连接后重试。', true);
      throw error;
    }
  } finally {
    if (requestId === accountRequest) setDashboardLoading(false);
  }
}
async function loadDashboard() {
  const requestId = ++dashboardRequest;
  dashboardAbort?.abort();
  dashboardAbort = new AbortController();
  state.dashboard = null;
  state.viewModel = null;
  setDashboardLoading(true);
  try {
    setStatus('正在读取健康记录');
    const payload = await apiRequest('/api/dashboard?tables=none', { signal: dashboardAbort.signal });
    if (requestId !== dashboardRequest) return;
    state.dashboard = payload;
    state.viewModel = normalizeDashboard(payload, state.admin.authenticated);
    renderDashboard();
    setStatus(state.account.authorized ? '健康记录已更新' : '记录已载入 · 待授权 Google Health');
  } catch (error) {
    if (error.name === 'AbortError' || requestId !== dashboardRequest) return;
    renderEmptyState('读取失败，请检查连接后重试。', true);
    throw error;
  } finally {
    if (requestId === dashboardRequest) setDashboardLoading(false);
  }
}
function renderEmptyState(message = '暂无可显示的数据。', retry = false) {
  destroyAllCharts();
  refs.dashboardNotice.hidden = false;
  refs.dashboardNoticeText.textContent = message;
  refs.retryBtn.hidden = !retry;
  refs.overviewMetrics.hidden = true;
  refs.dataQualityNote.hidden = true;
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
  views[state.activeView]();
  views.note();
  requestAnimationFrame(resizeVisibleCharts);
}
function getRouteState() { return Object.fromEntries(new URLSearchParams(window.location.search)); }
function sanitizeRange(value) { return VALID_RANGES.has(String(value)) ? String(value) : '30'; }
function syncRoute(values, { mode = 'replace' } = {}) {
  const url = new URL(window.location.href);
  url.searchParams.delete('profile');
  for (const [key, value] of Object.entries(values)) value == null || value === '' ? url.searchParams.delete(key) : url.searchParams.set(key, value);
  if (url.href !== window.location.href) window.history[mode === 'push' ? 'pushState' : 'replaceState']({}, '', url);
}
function activateView(view, { updateHistory = true, historyMode = 'replace' } = {}) {
  view = VIEW_ALIASES[view] || view;
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
  syncRoute({ view: state.activeView, range: refs.rangeSelect.value });
}
function openModal(id) { if (refs[id] && !refs[id].open) refs[id].showModal(); }
function closeModal(id) { if (refs[id]?.open) refs[id].close(); }


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
  if (state.admin.authenticated !== Boolean(payload.authenticated)) {
    accountRequest++;
    accountAbort?.abort();
    invalidateDashboard();
    stopFetchPolling();
    renderEmptyState('正在更新登录状态…');
  }
  state.admin.configured = Boolean(payload.configured)
  state.admin.authenticated = Boolean(payload.authenticated)
  state.admin.private = payload.data_access !== 'public'
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
        ? "已登录"
        : state.admin.private ? "私有健康记录" : "公开只读"
  }

  if (refs.adminLoginBtn) {
    refs.adminLoginBtn.classList.toggle("hidden", !configured || authenticated)
    refs.adminLoginBtn.disabled = !configured
    refs.adminLoginBtn.textContent = configured ? "登录" : "管理未配置"
  }

  refs.adminLogoutBtn?.classList.toggle("hidden", !authenticated)
  refs.openAccountBtn?.classList.toggle("hidden", !authenticated)
  refs.syncBtn?.classList.toggle("hidden", !authenticated)
  refs.reloadBtn?.classList.toggle("hidden", !authenticated)

  if (!authenticated) {
    closeModal("accountModal")
    closeModal("authModal")
  }

  renderAccountSettings()
}

function openAdminModal() {
  if (!state.admin.configured) {
    showToast("尚未配置登录口令，请先完成服务端设置。", true)
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
    showToast("请输入登录口令。", true)
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
    await refreshAccount()
    showToast("已登录。")
    setStatus("已登录")
  } finally {
    setButtonState(refs.adminLoginSubmit, false, "登录")
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
  await refreshAccount()
  showToast("已退出登录。")
}

function renderAccountSettings() {
  const { configured, authorized } = state.account;
  refs.openAccountBtn.textContent = configured ? '账户设置' : '连接 Google Health';
  refs.accountConnectionStatus.textContent = !configured ? '尚未连接 Google Health' : authorized ? 'Google Health 已授权' : '已保存 Google 客户端，等待授权';
  refs.accountSettingsHint.textContent = configured ? '需要更换 Google OAuth 客户端时填写新信息；已有健康记录和当前授权会保留，直到新授权完成。' : '填写 Google Cloud 中启用了 Google Health API 的 Web OAuth 客户端，然后完成授权。';
  refs.accountSetupSubmit.textContent = configured ? '保存并授权' : '连接并授权';
  refs.reauthorizeBtn.hidden = !configured;
  refs.syncBtn.disabled = !configured || !authorized || Boolean(state.fetchJobId);
  refs.reloadBtn.disabled = !state.account.has_data;
}
async function setupAccount() {
  const clientId = refs.clientId.value.trim();
  const clientSecret = refs.clientSecret.value.trim();
  const redirectUri = refs.redirectUri.value.trim();
  if (!clientId || !clientSecret) { showToast('请填写 Google Client ID 和 Client Secret。', true); return; }
  setButtonState(refs.accountSetupSubmit, true, '保存中…');
  try {
    await apiRequest('/api/account/setup', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ clientId, clientSecret, redirectUri }), requireAdmin: true,
    });
    refs.accountSetupForm.reset();
    await refreshAccount();
    await startAuthorization();
  } finally {
    setButtonState(refs.accountSetupSubmit, false);
    renderAccountSettings();
  }
}
async function startAuthorization() {
  let payload;
  try {
    payload = await apiRequest('/api/authorize', { requireAdmin: true });
  } catch (error) {
    if (error.code === 'google_credentials_missing') {
      renderAccountSettings();
      closeModal('authModal');
      openModal('accountModal');
    }
    throw error;
  }
  state.authorizationOpen = true;
  refs.authOpenLink.href = payload.auth_url || '#';
  refs.authRedirectValue.value = '';
  closeModal('accountModal');
  openModal('authModal');
}
async function submitAuthorization() {
  if (!state.authorizationOpen) return;
  const redirectUrl = refs.authRedirectValue.value.trim();
  if (!redirectUrl) { showToast('请粘贴完整回调 URL（包括 code 和 state）。', true); return; }
  setButtonState(refs.authExchangeSubmit, true, '提交中…');
  try {
    const result = await apiRequest('/api/authorize-exchange', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ redirectUrl }), requireAdmin: true,
    });
    state.authorizationOpen = false;
    closeModal('authModal');
    await refreshAccount();
    showToast(result.warning || 'Google Health 授权完成，现在可以同步数据。', Boolean(result.warning));
  } catch (error) {
    if (['account_not_linked', 'account_mismatch', 'missing_permissions', 'oauth_exchange_failed',
      'oauth_state_expired', 'oauth_consent_denied', 'oauth_network_error', 'identity_check_failed',
      'refresh_token_missing'].includes(error.code)) {
      // The server consumes a callback once. Offer a fresh consent request for retry.
      try { await startAuthorization(); } catch { /* Keep the original actionable error. */ }
    }
    throw error;
  } finally { setButtonState(refs.authExchangeSubmit, false, '提交授权结果'); }
}

async function rebuildDashboard() {
  if (!state.account.has_data) {
    showToast("请先同步健康数据。", true)
    return
  }
  setStatus("正在刷新健康视图")
  await apiRequest("/api/rebuild-dashboard", {
    method: "POST",
    requireAdmin: true,
  })
  await refreshAccount()
  showToast("健康视图已刷新。")
}

async function startFetch() {
  if (!state.account.configured) {
    showToast("请先连接 Google Health。", true)
    return
  }

  const payload = await apiRequest("/api/fetch-data", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
    requireAdmin: true,
  })

  state.fetchJobId = payload.job_id
  state.fetchNotFoundCount = 0
  setButtonState(refs.syncBtn, true, "同步中...")
  setStatus("已发起同步任务")
  pollFetchStatus()
}

function stopFetchPolling() {
  clearInterval(state.fetchTimer);
  state.fetchTimer = null;
  state.fetchJobId = null;
  state.authorizationOpen = false;
  setButtonState(refs.syncBtn, false, '同步数据');
}
function pollFetchStatus() {
  if (!state.fetchJobId) return
  clearInterval(state.fetchTimer)
  const jobId = state.fetchJobId;
  state.fetchTimer = setInterval(async () => {
    try {
      const payload = await apiRequest(`/api/fetch-status/${encodeURIComponent(jobId)}`, {
        ignore404: true,
        requireAdmin: true,
      })
      if (state.fetchJobId !== jobId || !state.admin.authenticated) return;
      if (!payload) {
        state.fetchNotFoundCount += 1
        if (state.fetchNotFoundCount >= 3) {
          clearInterval(state.fetchTimer)
          state.fetchTimer = null
          state.fetchJobId = null
          setButtonState(refs.syncBtn, false, "同步数据")
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
        setButtonState(refs.syncBtn, false, "同步数据")
        if (payload.status === "completed") {
          setStatus("同步完成，正在更新记录")
          await refreshAccount()
          showToast("健康数据已同步完成。")
        } else {
          showToast(payload.error || "同步失败。", true)
          setStatus("同步失败")
        }
      }
    } catch (error) {
      if (state.fetchJobId !== jobId) return;
      clearInterval(state.fetchTimer)
      state.fetchTimer = null
      state.fetchJobId = null
      setButtonState(refs.syncBtn, false, "同步数据")
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
  if (payload.throttle_active) {
    parts.push(payload.throttle_mmss ? `限流倒计时 ${payload.throttle_mmss}` : "数据服务限流中")
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
