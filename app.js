import { formatDate, formatDateTime } from './js/format.js';
import { VIEWS, VIEW_ALIASES, normalizeDashboard } from './js/data.js';
import { destroyAllCharts, resizeVisibleCharts } from './js/charts.js';
import { createViews } from './js/views.js';
import { createTableRenderer } from './js/table.js';

const state = {
  account: { profile_id: null, has_data: false }, dashboard: null, viewModel: null, revision: 0,
  activeView: 'overview', monthlyMetric: 'steps',
};
const refs = Object.fromEntries([...document.querySelectorAll('[id]')].map(element => [element.id, element]));
const renderTable = createTableRenderer({ apiRequest, getContext: () => ({ revision: state.revision, version: state.dashboard?.generated_at }) });
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
  await refreshAccount();
}
function bindEvents() {
  refs.rangeSelect.addEventListener('change', () => {
    syncRoute({ range: refs.rangeSelect.value }, { mode: 'push' });
    renderDashboard();
  });
  for (const id of ['reloadBtn', 'retryBtn']) {
    refs[id].addEventListener('click', () => refreshAccount().catch(handleAsyncError));
  }
  document.querySelectorAll('.tab-button').forEach(button => {
    button.addEventListener('click', () => activateView(button.dataset.view, { historyMode: 'push' }));
    button.addEventListener('keydown', handleTabKeydown);
  });
  document.addEventListener('click', event => {
    const target = event.target.closest('button');
    if (!target) return;
    if (target.dataset.jumpView) activateView(target.dataset.jumpView, { historyMode: 'push' });
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
    state.account = { profile_id: payload.profile_id || null, has_data: Boolean(payload.has_data) };
    if (!state.account.has_data) {
      renderEmptyState('暂无健康记录，稍后刷新查看。');
      setStatus('暂无记录');
      return;
    }
    await loadDashboard();
  } catch (error) {
    if (error.name === 'AbortError' || requestId !== accountRequest) return;
    renderEmptyState('读取失败，请检查连接后重试。', true);
    throw error;
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
    state.viewModel = normalizeDashboard(payload);
    renderDashboard();
    setStatus('健康记录已更新');
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
async function apiRequest(path, options = {}) {
  const response = await fetch(path, { method: 'GET', credentials: 'omit', signal: options.signal });
  const contentType = response.headers.get('content-type') || '';
  const payload = contentType.includes('application/json') ? await response.json() : await response.text();
  if (!response.ok) {
    const message = typeof payload === 'string' ? `请求失败 (${response.status})` : payload.error || payload.message || '请求失败';
    const error = new Error(typeof message === 'object' ? message.message || '请求失败' : message);
    error.status = response.status;
    throw error;
  }
  return payload;
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

function showToast(message, isError = false) {
  if (!refs.toast) return
  document.body.append(refs.toast)
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
