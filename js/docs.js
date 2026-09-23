import { buildRequest, exampleCode, readPreview } from './docs-request.js';

const endpoints = JSON.parse(document.getElementById('apiDefinitions').textContent);
const byId = new Map(endpoints.map(item => [item.id, item]));
const refs = Object.fromEntries([...document.querySelectorAll('[id]')].map(node => [node.id, node]));
const cards = [...document.querySelectorAll('.endpoint')];
let activeRequest, requestSequence = 0, noticeTimer;

function notify(text) {
  refs.copyStatus.textContent = text;
  clearTimeout(noticeTimer);
  noticeTimer = setTimeout(() => { refs.copyStatus.textContent = ''; }, 4000);
}
function cancelRequest(message = '请求已取消。') {
  requestSequence++;
  activeRequest?.abort();
  if (activeRequest) refs.requestStatus.textContent = message;
  activeRequest = null;
  refs.sendRequest.disabled = false;
  refs.cancelRequest.hidden = true;
}
function clearResponse() {
  refs.responsePreview.textContent = '';
  refs.responsePreview.hidden = true;
  refs.requestStatus.textContent = '尚未发送请求。';
}
function currentValues() {
  return Object.fromEntries(new FormData(refs.requestForm));
}
function updateCode() {
  const values = currentValues();
  try {
    const url = buildRequest(byId.get(refs.endpointSelect.value), values, location.origin);
    refs.requestCode.textContent = exampleCode(url, refs.exampleLanguage.value);
  } catch (error) { refs.requestCode.textContent = error.message; }
}
function updateExample() {
  cancelRequest();
  clearResponse();
  updateCode();
}
function chooseEndpoint(id) {
  const endpoint = byId.get(id);
  if (!endpoint) return;
  refs.endpointSelect.value = id;
  refs.requestFields.replaceChildren();
  for (const parameter of endpoint.parameters) {
    const wrapper = document.createElement('div');
    wrapper.className = 'request-field';
    const label = document.createElement('label');
    const input = document.createElement(parameter.schema.enum ? 'select' : 'input');
    input.id = `request-${parameter.name}`;
    input.name = parameter.name;
    input.required = parameter.required;
    label.htmlFor = input.id;
    label.textContent = `${parameter.name} · ${parameter.in === 'path' ? '路径' : '查询，可选'}`;
    input.title = parameter.description;
    if (parameter.schema.enum) {
      if (!parameter.required) input.add(new Option('默认（不传）', ''));
      for (const value of parameter.schema.enum) input.add(new Option(value, value));
    } else {
      input.type = parameter.schema.type === 'integer' ? 'number' : 'text';
      input.autocomplete = 'off';
      for (const key of ['minimum', 'maximum', 'pattern']) {
        if (parameter.schema[key] !== undefined) input.setAttribute(({minimum:'min',maximum:'max',pattern:'pattern'})[key], parameter.schema[key]);
      }
      if (input.type === 'number') input.step = '1';
    }
    input.value = parameter.example ?? '';
    wrapper.append(label, input);
    refs.requestFields.append(wrapper);
  }
  updateExample();
}
function filterEndpoints() {
  const words = refs.endpointSearch.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
  let count = 0;
  for (const card of cards) {
    const match = words.every(word => card.dataset.search.toLowerCase().includes(word));
    card.hidden = !match;
    if (match) count++;
  }
  document.querySelectorAll('.endpoint-group').forEach(group => {
    group.hidden = [...group.querySelectorAll('.endpoint')].every(card => card.hidden);
  });
  refs.searchCount.textContent = `${count} / ${endpoints.length} 个接口`;
  refs.noResults.hidden = count !== 0;
}
function revealAnchor() {
  let id;
  try { id = decodeURIComponent(location.hash.slice(1)); } catch { return; }
  const target = document.getElementById(id);
  if (!target) return;
  if (target.classList.contains('endpoint') || target.classList.contains('endpoint-group')) {
    refs.endpointSearch.value = '';
    filterEndpoints();
  }
  if (target.tagName === 'DETAILS') target.open = true;
  if (id === 'try') refs.requestBuilder.open = true;
  if (location.hash) target.scrollIntoView({ block: 'start' });
}
async function copyCode(id) {
  const block = document.getElementById(id);
  if (!block) return;
  try {
    if (!navigator.clipboard?.writeText) throw new Error('Clipboard unavailable');
    await navigator.clipboard.writeText(block.textContent);
    notify('示例已复制。');
  } catch {
    const range = document.createRange();
    range.selectNodeContents(block);
    getSelection()?.removeAllRanges();
    getSelection()?.addRange(range);
    block.focus();
    notify('浏览器未允许复制；已选中示例，请手动复制。');
  }
}
async function sendRequest(event) {
  event.preventDefault();
  cancelRequest();
  clearResponse();
  const endpoint = byId.get(refs.endpointSelect.value);
  const values = currentValues();

  let url;
  try { url = buildRequest(endpoint, values, location.origin); }
  catch (error) { refs.requestStatus.textContent = error.message; return; }
  const sequence = requestSequence;
  const controller = new AbortController();
  activeRequest = controller;
  const timeout = setTimeout(() => controller.abort('timeout'), 15000);
  refs.sendRequest.disabled = true;
  refs.cancelRequest.hidden = false;
  refs.requestStatus.textContent = '正在读取本站缓存…';
  const start = performance.now();
  try {
    const response = await fetch(url, { method: 'GET', credentials: 'same-origin', cache: 'no-store', redirect: 'error', signal: controller.signal });
    const preview = await readPreview(response);
    if (sequence !== requestSequence) return;
    const contentType = response.headers.get('content-type')?.split(';')[0] || '未知类型';
    refs.responsePreview.textContent = preview.text || '（空响应）';
    refs.responsePreview.hidden = false;
    refs.requestStatus.textContent = `HTTP ${response.status} · ${contentType} · ${Math.round(performance.now() - start)} ms` +
      (preview.truncated ? ' · 预览已截断至 64 KiB。' : '') +
      (response.status === 401 ? ' · 请先在本站仪表盘登录。' : '');
  } catch (error) {
    if (sequence === requestSequence) refs.requestStatus.textContent = controller.signal.reason === 'timeout'
      ? '请求超过 15 秒，已取消。' : '请求失败，请检查连接后手动重试。';
  } finally {
    clearTimeout(timeout);
    if (sequence === requestSequence) {
      activeRequest = null;
      refs.sendRequest.disabled = false;
      refs.cancelRequest.hidden = true;
    }
  }
}

refs.endpointSearch.addEventListener('input', filterEndpoints);
refs.endpointSelect.addEventListener('change', () => chooseEndpoint(refs.endpointSelect.value));
refs.requestFields.addEventListener('input', updateExample);
refs.exampleLanguage.addEventListener('change', updateCode);
refs.requestForm.addEventListener('submit', sendRequest);
refs.cancelRequest.addEventListener('click', () => cancelRequest());
document.addEventListener('click', event => {
  const button = event.target.closest('button');
  if (button?.dataset.copy) copyCode(button.dataset.copy);
  if (button?.dataset.useEndpoint) {
    chooseEndpoint(button.dataset.useEndpoint);
    refs.requestBuilder.open = true;
    location.hash = 'try';
    refs.requestBuilder.scrollIntoView({block:'start'});
    refs.endpointSelect.focus({preventScroll:true});
  }
  const anchor = event.target.closest('a[href^="#"]');
  if (anchor) {
    const target = document.getElementById(anchor.getAttribute('href').slice(1));
    if (target?.classList.contains('endpoint-group')) { refs.endpointSearch.value = ''; filterEndpoints(); }
  }
});
document.addEventListener('keydown', event => {
  if (event.key === '/' && !event.ctrlKey && !event.metaKey && !event.altKey && !event.target.matches('input,select,textarea,[contenteditable]')) {
    event.preventDefault(); refs.endpointSearch.focus();
  }
  if (event.key === 'Escape' && event.target === refs.endpointSearch) { refs.endpointSearch.value = ''; filterEndpoints(); }
});
window.addEventListener('hashchange', revealAnchor);
window.addEventListener('pagehide', () => { cancelRequest(); clearResponse(); });
for (const block of document.querySelectorAll('[data-example-path]')) {
  block.textContent = exampleCode(new URL(block.dataset.examplePath, location.origin).href);
}
chooseEndpoint('profile');
document.querySelectorAll('[data-enhanced]').forEach(node => { node.hidden = false; });
revealAnchor();
