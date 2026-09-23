const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const outputDir = process.env.TEST_OUTPUT_DIR || 'test-results';
fs.mkdirSync(outputDir, { recursive: true });
const base = process.env.BASE_URL || 'http://127.0.0.1:9001';
const fixtureProfile = process.env.TEST_PROFILE || 'Demo';
const report = { base, layouts: [], checks: [] };
const viewNames = ['overview', 'sleep', 'activity', 'recovery', 'body', 'lifestyle', 'account', 'family'];
const check = name => report.checks.push(name);
async function ready(page) {
  await page.waitForSelector('#mainContent[aria-busy="false"]');
  await page.waitForTimeout(150);
}
async function layout(page) {
  return page.evaluate(() => ({
    height: document.body.scrollHeight, width: document.documentElement.scrollWidth,
    viewport: innerWidth, visiblePanels: [...document.querySelectorAll('[data-view-panel]')].filter(panel => !panel.hidden).length,
    visibleCharts: [...document.querySelectorAll('canvas')].filter(canvas => canvas.offsetParent !== null).map(canvas => ({ width: canvas.clientWidth, height: canvas.clientHeight })),
  }));
}
async function main() {
  const browser = await chromium.launch({ headless: true });
  try {
    for (const [name, width, height] of [['desktop', 1440, 1000], ['mobile', 390, 844]]) {
      const page = await browser.newPage({ viewport: { width, height } });
      const errors = [], external = [];
      page.on('pageerror', error => errors.push(error.message));
      page.on('request', request => { if (!request.url().startsWith(base)) external.push(request.url()); });
      await page.goto(base, { waitUntil: 'networkidle' });
      await ready(page);
      assert.equal(await page.locator('#dashboardNotice').isVisible(), false);
      assert.equal(await page.locator('#statsGrid > button').count(), 4);
      const initialRequests = await page.evaluate(() => performance.getEntriesByType('resource').map(entry => entry.name));
      assert.equal(initialRequests.some(url => url.includes('/api/profile-summaries')), false);
      assert.equal(initialRequests.some(url => url.includes('/api/tables/')), false);
      check(`${name}: homepage skips profile summaries and record tables`);
      const initial = await layout(page);
      await page.screenshot({ path: path.join(outputDir, `after-${name}.png`), fullPage: true });
      const chartId = await page.evaluate(() => window.Chart.getChart('overviewTrendChart')?.id);
      for (const range of ['14', '90', '30']) {
        await page.selectOption('#rangeSelect', range);
        await page.waitForTimeout(150);
        assert.equal(await page.evaluate(() => window.Chart.getChart('overviewTrendChart')?.id), chartId);
      }
      check(`${name}: range changes reuse chart instances`);
      for (const view of viewNames) {
        await page.locator(`#tab-${view}`).click();
        await page.waitForTimeout(150);
        assert.equal(await page.locator(`#panel-${view}`).isVisible(), true, `${name}/${view} hidden`);
        const info = await layout(page);
        assert.ok(info.width <= width + 1, `${name}/${view} horizontal overflow: ${info.width}`);
        assert.equal(info.visiblePanels, 1);
        for (const chart of info.visibleCharts) assert.ok(chart.width > 100 && chart.height > 100, `${view} chart size invalid`);
      }
      check(`${name}: all eight views render without overflow`);
      await page.locator('#tab-sleep').click();
      await page.locator('#sleepTableWrap').evaluate(element => { const details = element.closest('details'); if (details) details.open = true; });
      await page.waitForSelector('#sleepTableWrap tbody tr');
      if (process.env.SYNTHETIC_FIXTURE === '1') {
        assert.equal(await page.locator('#sleepTableWrap tbody tr').count(), 20);
        const first = await page.locator('#sleepTableWrap tbody tr').first().innerText();
        await page.locator('#sleepTableWrap [data-page="next"]').click();
        await page.waitForFunction(previous => document.querySelector('#sleepTableWrap tbody tr')?.innerText !== previous && document.querySelector('#sleepTableWrap tbody tr'), first);
        assert.equal(await page.locator('#sleepTableWrap tbody tr').count(), 20);
        await page.locator('#sleepTableWrap [data-page="next"]').click();
        await page.waitForFunction(() => document.querySelectorAll('#sleepTableWrap tbody tr').length === 15);
        assert.equal(await page.locator('#sleepTableWrap [data-page="next"]').isDisabled(), true);
        check(`${name}: server pagination covers 20 + 20 + 15 records`);
      }
      await page.locator('#adminLoginBtn').click();
      assert.equal(await page.locator('#adminModal').isVisible(), true);
      assert.equal(await page.locator('#adminPasswordInput').evaluate(input => input === document.activeElement), true);
      await page.keyboard.press('Escape');
      assert.equal(await page.locator('#adminModal').isVisible(), false);
      assert.equal(await page.locator('#adminLoginBtn').evaluate(button => button === document.activeElement), true);
      check(`${name}: native dialog focus and Escape`);
      await page.locator('#tab-overview').click();
      await page.locator('#tab-overview').focus();
      await page.keyboard.press('ArrowRight');
      assert.equal(await page.locator('#tab-sleep').getAttribute('aria-selected'), 'true');
      await page.goBack();
      assert.equal(await page.locator('#tab-overview').getAttribute('aria-selected'), 'true');
      check(`${name}: keyboard tabs and browser history`);
      assert.deepEqual(errors, []);
      assert.deepEqual(external, []);
      report.layouts.push({ name, ...initial, errors, externalRequests: external.length });
      await page.close();
    }
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(`${base}/?view=recovery&range=14`, { waitUntil: 'networkidle' });
    await ready(page);
    assert.equal(await page.locator('#panel-recovery').isVisible(), true);
    assert.equal(await page.locator('#rangeSelect').inputValue(), '14');
    check('deep links preserve view and range');
    for (const path of ['/.env', '/server.py', '/profiles/index.json', '/js/not-public.txt', '/script.js', '/ui-cn.js']) {
      const response = await page.request.get(base + path);
      assert.equal(response.status(), 404, path);
    }
    assert.match((await page.request.get(base + '/')).headers()['cache-control'], /no-cache/);
    assert.match((await page.request.get(base + '/vendor/chart.umd-4.4.1.min.js')).headers()['cache-control'], /immutable/);
    assert.match((await page.request.get(base + '/api/admin/session')).headers()['cache-control'], /no-store/);
    assert.equal((await page.request.post(base + '/api/create-profile', { data: {} })).status(), 401);
    check('static allowlist, asset caching and anonymous write protection');
    if (process.env.PREVIEW_ADMIN_PASSWORD) {
      await page.locator('#adminLoginBtn').click();
      await page.fill('#adminPasswordInput', process.env.PREVIEW_ADMIN_PASSWORD);
      await page.locator('#adminLoginSubmit').click();
      await page.waitForSelector('#adminLogoutBtn:not(.hidden)');
      assert.match((await page.request.get(base + '/api/dashboard/' + fixtureProfile)).headers()['cache-control'], /private, no-store/);
      assert.equal((await page.request.post(base + '/api/admin/logout')).status(), 403);
      await page.locator('#openManagerBtn').click();
      assert.equal(await page.locator('#newClientSecret').getAttribute('type'), 'password');
      await page.keyboard.press('Escape');
      await page.locator('#adminLogoutBtn').click();
      await page.waitForSelector('#adminLoginBtn:not(.hidden)');
      assert.equal(await page.locator('#syncBtn').isVisible(), false);
      check('preview login/logout, secret input and CSRF rejection');
    }
    await page.route('**/api/dashboard/**', route => route.fulfill({ status: 500, contentType: 'application/json', body: '{"error":"Synthetic failure"}' }));
    await page.reload({ waitUntil: 'networkidle' });
    await ready(page);
    assert.equal(await page.locator('#retryBtn').isVisible(), true);
    await page.unroute('**/api/dashboard/**');
    await page.locator('#retryBtn').click();
    await page.waitForSelector('#dashboardNotice', { state: 'hidden' });
    check('failed requests show retry and recover');
    await page.route('**/vendor/chart.umd-4.4.1.min.js', route => route.abort());
    await page.reload({ waitUntil: 'networkidle' });
    await ready(page);
    assert.match(await page.locator('.chart-empty:visible').first().innerText(), /组件加载失败/);
    await page.unroute('**/vendor/chart.umd-4.4.1.min.js');
    check('missing chart library degrades without crashing');
    await page.route('**/api/profiles', route => route.fulfill({ contentType: 'application/json', body: '[]' }));
    await page.reload({ waitUntil: 'networkidle' });
    await ready(page);
    assert.equal(await page.locator('#profileSelect').isDisabled(), true);
    assert.equal(await page.locator('#dashboardNotice').isVisible(), true);
    check('empty profile state');
    await page.close();
    const racePage = await browser.newPage();
    racePage.on('pageerror', error => errors.push(error.message));
    await racePage.route('**/api/profiles', route => route.fulfill({ contentType: 'application/json', body: '[{"name":"Slow"},{"name":"Fast"}]' }));
    await racePage.route('**/api/profile-summaries', route => route.fulfill({ contentType: 'application/json', body: '[]' }));
    let slowStarted = false;
    await racePage.route('**/api/dashboard/*', async route => {
      const slow = new URL(route.request().url()).pathname.endsWith('/Slow');
      if (slow) slowStarted = true;
      await new Promise(resolve => setTimeout(resolve, slow ? 1000 : 20));
      await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ profile: { display_name: slow ? 'Slow' : 'Fast' }, overview: { latest_date: slow ? '2026-09-01' : '2026-09-23' }, charts: { daily: [] } }) }).catch(() => {});
    });
    await racePage.goto(base, { waitUntil: 'domcontentloaded' });
    await racePage.waitForFunction(() => document.querySelector('#profileSelect')?.value === 'Slow');
    await racePage.waitForTimeout(100);
    assert.equal(slowStarted, true);
    await racePage.selectOption('#profileSelect', 'Fast');
    await ready(racePage);
    await racePage.waitForTimeout(1100);
    assert.equal(await racePage.locator('#profileSelect').inputValue(), 'Fast');
    assert.match(await racePage.locator('#latestRecordText').innerText(), /23/);
    assert.equal(await racePage.locator('#statsGrid > button').count(), 4);
    check('late responses cannot overwrite the selected profile; empty datasets render safely');
    await racePage.close();
    assert.deepEqual(errors, []);
  } finally {
    await browser.close();
    fs.writeFileSync(path.join(outputDir, 'browser-report.json'), JSON.stringify(report, null, 2));
  }
  console.log(JSON.stringify(report, null, 2));
}
main().catch(error => { console.error(error); process.exitCode = 1; });
