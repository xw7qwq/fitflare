import { escapeHtml } from './format.js';

const TABLE_KEYS = {
  sleepTableWrap: 'sleep', activityTableWrap: 'activity', activityLogTableWrap: 'activity_logs',
  recoveryTableWrap: 'recovery', bodyTableWrap: 'body', vitalTableWrap: 'vitals',
  foodTableWrap: 'foods', deviceTableWrap: 'devices', badgeTableWrap: 'badges',
  alarmTableWrap: 'alarms', endpointTableWrap: 'endpoints',
};
export function pageBounds(total, offset = 0, size = 20) {
  const count = Math.max(0, Number(total) || 0);
  const start = Math.max(0, Number(offset) || 0);
  return { start: count ? start + 1 : 0, end: Math.min(start + size, count), previous: start > 0, next: start + size < count };
}

export function createTableRenderer({ getContext, apiRequest }) {
  const tables = new WeakMap();
  return function renderTable(container, columns, unusedRows, formatters = {}) {
    if (!container) return;
    const context = getContext();
    const key = `${context.revision}:${context.version}:${context.authenticated}`;
    let table = tables.get(container);
    if (table?.key === key) return;
    table?.controller?.abort();
    table = { key, offset: 0, loaded: false, loading: false, controller: null };
    tables.set(container, table);
    container.innerHTML = '';
    const details = container.closest('details');
    // Replace the prior listener instead of accumulating handlers when data refreshes.
    if (details) details.ontoggle = () => { if (details.open && !table.loaded && !table.loading) load(); };

    function current() {
      const now = getContext();
      return tables.get(container) === table && `${now.revision}:${now.version}:${now.authenticated}` === key;
    }
    async function load() {
      if (!current() || table.loading) return;
      table.loading = true;
      table.controller = new AbortController();
      container.setAttribute('aria-busy', 'true');
      container.innerHTML = '<div class="empty-state">正在读取记录…</div>';
      try {
        const path = `/api/tables/${TABLE_KEYS[container.id]}?offset=${table.offset}&limit=20`;
        const result = await apiRequest(path, { signal: table.controller.signal });
        if (!current()) return;
        const rows = Array.isArray(result.rows) ? result.rows : [];
        const page = pageBounds(result.meta?.total, table.offset);
        const head = columns.map(column => `<th scope="col">${escapeHtml(column.label)}</th>`).join('');
        const body = rows.map(row => `<tr>${columns.map(column => {
          const value = formatters[column.key] ? formatters[column.key](row[column.key], row) : row[column.key];
          return `<td>${escapeHtml(value == null || value === '' ? '--' : String(value))}</td>`;
        }).join('')}</tr>`).join('');
        container.innerHTML = rows.length ? `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>` : '<div class="empty-state">当前没有可显示的数据。</div>';
        if (result.meta?.total > 20) {
          container.insertAdjacentHTML('beforeend', `<nav class="table-pagination" aria-label="${escapeHtml(columns[0]?.label || '记录')}分页">
            <button type="button" class="button" data-page="previous" ${page.previous ? '' : 'disabled'}>上一页</button>
            <span role="status">${page.start}–${page.end} / ${escapeHtml(result.meta.total)} 条</span>
            <button type="button" class="button" data-page="next" ${page.next ? '' : 'disabled'}>下一页</button></nav>`);
        }
        table.loaded = true;
      } catch (error) {
        if (!current() || error.name === 'AbortError') return;
        container.innerHTML = '<div class="empty-state">记录读取失败。<button type="button" class="button" data-page="retry">重试</button></div>';
      } finally {
        table.loading = false;
        if (current()) container.setAttribute('aria-busy', 'false');
      }
    }
    container.onclick = event => {
      const action = event.target.closest('[data-page]')?.dataset.page;
      if (!action || table.loading) return;
      if (action !== 'retry') table.offset = Math.max(0, table.offset + (action === 'next' ? 20 : -20));
      load();
    };
    if (!details || details.open) load();
  };
}
