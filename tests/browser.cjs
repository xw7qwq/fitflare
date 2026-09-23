const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const outputDir = process.env.TEST_OUTPUT_DIR || 'test-results';
fs.mkdirSync(outputDir, { recursive: true });
const base = process.env.BASE_URL || 'http://127.0.0.1:9001';
const report = { base, layouts: [], checks: [] };
const viewNames = ['overview', 'sleep', 'activity', 'recovery', 'body', 'lifestyle', 'account'];
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
  const browser = await chromium.launch({ headless: true, ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH } : {}) });
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
      assert.equal(await page.locator('#profileSelect, #tab-family, #panel-family, #existingProfilesList, #newProfileName').count(), 0);
      assert.equal(await page.locator('[data-view]').count(), 7);
      assert.match(await page.title(), /Fitflare/);
      assert.equal(initialRequests.some(url => /\/api\/(profiles|profile-summaries|dashboard\/)/.test(url)), false);
      check(`${name}: seven personal views; no profile controls or legacy profile requests; homepage defers record tables`);
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
      check(`${name}: all seven views render without overflow`);
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
    await page.goto(`${base}/?view=recovery&range=14&profile=Other`, { waitUntil: 'networkidle' });
    await ready(page);
    assert.equal(await page.locator('#panel-recovery').isVisible(), true);
    assert.equal(await page.locator('#rangeSelect').inputValue(), '14');
    assert.equal(new URL(page.url()).searchParams.has('profile'), false);
    check('deep links preserve view and range and remove legacy profile selection');
    for (const path of ['/.env', '/server.py', '/profiles/index.json', '/js/not-public.txt', '/script.js', '/ui-cn.js']) {
      const response = await page.request.get(base + path);
      assert.equal(response.status(), 404, path);
    }
    assert.match((await page.request.get(base + '/')).headers()['cache-control'], /no-cache/);
    assert.match((await page.request.get(base + '/vendor/chart.umd-4.4.1.min.js')).headers()['cache-control'], /immutable/);
    assert.match((await page.request.get(base + '/api/admin/session')).headers()['cache-control'], /no-store/);
    assert.equal((await page.request.post(base + '/api/account/setup', { data: {} })).status(), 401);
    check('static allowlist, asset caching and anonymous write protection');
    if (process.env.PREVIEW_ADMIN_PASSWORD) {
      await page.locator('#adminLoginBtn').click();
      await page.fill('#adminPasswordInput', process.env.PREVIEW_ADMIN_PASSWORD);
      await page.locator('#adminLoginSubmit').click();
      await page.waitForSelector('#adminLogoutBtn:not(.hidden)');
      assert.match((await page.request.get(base + '/api/dashboard')).headers()['cache-control'], /private, no-store/);
      assert.equal((await page.request.post(base + '/api/admin/logout')).status(), 403);
      await page.locator('#openAccountBtn').click();
      assert.equal(await page.locator('#clientSecret').getAttribute('type'), 'password');
      assert.equal(await page.locator('#accountSetupForm input').count(), 2);
      assert.equal(await page.locator('#accountSetupForm input[type=text]').getAttribute('id'), 'clientId');
      assert.equal(await page.locator('#accountModal button').filter({ hasText: '删除' }).count(), 0);
      await page.keyboard.press('Escape');
      await page.locator('#adminLogoutBtn').click();
      await page.waitForSelector('#adminLoginBtn:not(.hidden)');
      assert.equal(await page.locator('#syncBtn').isVisible(), false);
      check('preview login/logout, secret input and CSRF rejection');
    }
    await page.route('**/api/dashboard?*', route => route.fulfill({ status: 500, contentType: 'application/json', body: '{"error":"Synthetic failure"}' }));
    await page.reload({ waitUntil: 'networkidle' });
    await ready(page);
    assert.equal(await page.locator('#retryBtn').isVisible(), true);
    await page.unroute('**/api/dashboard?*');
    await page.locator('#retryBtn').click();
    await page.waitForSelector('#dashboardNotice', { state: 'hidden' });
    check('failed requests show retry and recover');
    await page.route('**/vendor/chart.umd-4.4.1.min.js', route => route.abort());
    await page.reload({ waitUntil: 'networkidle' });
    await ready(page);
    assert.match(await page.locator('.chart-empty:visible').first().innerText(), /组件加载失败/);
    await page.unroute('**/vendor/chart.umd-4.4.1.min.js');
    check('missing chart library degrades without crashing');
    await page.route('**/api/account', route => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ configured: false, authorized: false, has_data: false }) }));
    await page.reload({ waitUntil: 'networkidle' });
    await ready(page);
    assert.equal(await page.locator('#dashboardNotice').isVisible(), true);
    assert.equal(await page.locator('#overviewMetrics').isVisible(), false);
    assert.match(await page.locator('#dashboardNoticeText').innerText(), /登录/);
    check('new installation shows a connection empty state');
    await page.close();

    if (process.env.PREVIEW_ADMIN_PASSWORD) {
      // All Fitbit-facing actions are intercepted. Only synthetic application values are submitted.
      const setupPage = await browser.newPage();
      setupPage.on('pageerror', error => errors.push(error.message));
      let configured = false, authorized = false;
      const setupBodies = [], exchangeBodies = [];
      await setupPage.route('**/api/account', route => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ profile_id: 'Demo', configured, authorized, has_data: false }) }));
      await setupPage.route('**/api/account/setup', async route => {
        setupBodies.push(route.request().postDataJSON());
        assert.ok(route.request().headers()['x-fitbaus-csrf']);
        configured = true;
        await route.fulfill({ contentType: 'application/json', body: '{}' });
      });
      await setupPage.route('**/api/authorize', route => route.fulfill({ contentType: 'application/json', body: '{"auth_url":"https://example.invalid/fitbit-consent"}' }));
      await setupPage.route('**/api/authorize-exchange', async route => {
        exchangeBodies.push(route.request().postDataJSON());
        authorized = true;
        await route.fulfill({ contentType: 'application/json', body: '{}' });
      });
      await setupPage.goto(base, { waitUntil: 'networkidle' });
      await setupPage.locator('#adminLoginBtn').click();
      await setupPage.fill('#adminPasswordInput', process.env.PREVIEW_ADMIN_PASSWORD);
      await setupPage.locator('#adminLoginSubmit').click();
      await setupPage.waitForSelector('#adminLogoutBtn:not(.hidden)');
      await ready(setupPage);
      assert.match(await setupPage.locator('#dashboardNoticeText').innerText(), /连接 Fitbit/);
      assert.equal(await setupPage.locator('#openAccountBtn').innerText(), '连接 Fitbit');
      await setupPage.locator('#openAccountBtn').click();
      assert.equal(await setupPage.locator('#accountSetupForm input').count(), 2);
      assert.equal(await setupPage.locator('#reauthorizeBtn').isVisible(), false);
      await setupPage.fill('#clientId', 'synthetic-client');
      await setupPage.fill('#clientSecret', 'synthetic-secret');
      await setupPage.locator('#accountSetupSubmit').click();
      await setupPage.waitForSelector('#authModal[open]');
      assert.deepEqual(setupBodies, [{ clientId: 'synthetic-client', clientSecret: 'synthetic-secret' }]);
      assert.equal(await setupPage.locator('#clientSecret').inputValue(), '');
      await setupPage.fill('#authRedirectValue', 'synthetic-code');
      await setupPage.locator('#authExchangeSubmit').click();
      await setupPage.waitForSelector('#authModal', { state: 'hidden' });
      await setupPage.waitForFunction(() => document.querySelector('#dashboardNoticeText').textContent.includes('同步 Fitbit'));
      assert.deepEqual(exchangeBodies, [{ redirectUrl: 'synthetic-code' }]);
      assert.equal(await setupPage.locator('#syncBtn').isDisabled(), false);
      await setupPage.locator('#openAccountBtn').click();
      assert.match(await setupPage.locator('#accountConnectionStatus').innerText(), /已授权/);
      await setupPage.locator('#reauthorizeBtn').click();
      await setupPage.waitForSelector('#authModal[open]');
      assert.equal(await setupPage.locator('#authOpenLink').getAttribute('href'), 'https://example.invalid/fitbit-consent');
      check('first-run setup takes only application credentials, finishes mocked authorization, enables sync and supports reauthorization');
      await setupPage.close();
    }

    const racePage = await browser.newPage();
    racePage.on('pageerror', error => errors.push(error.message));
    let signedIn = false, requests = 0, slowStarted = false, tableStarted = false;
    const session = () => ({ configured: true, authenticated: signedIn, csrf_token: signedIn ? 'synthetic-csrf' : null, data_access: signedIn ? 'private' : 'public' });
    await racePage.route('**/api/admin/session', route => route.fulfill({ contentType: 'application/json', body: JSON.stringify(session()) }));
    await racePage.route('**/api/admin/login', route => {
      signedIn = true;
      return route.fulfill({ contentType: 'application/json', body: JSON.stringify(session()) });
    });
    await racePage.route('**/api/admin/logout', route => {
      signedIn = false;
      return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ ...session(), data_access: 'private' }) });
    });
    let loggedOut = false;
    await racePage.route('**/api/account', route => {
      if (loggedOut) return route.fulfill({ status: 401, contentType: 'application/json', body: '{"code":"private_data","error":"Private"}' });
      return route.fulfill({ contentType: 'application/json', body: '{"profile_id":"Demo","configured":true,"authorized":true,"has_data":true}' });
    });
    await racePage.route('**/api/dashboard?*', async route => {
      const requestId = ++requests;
      const slow = requestId === 1 || requestId === 3;
      if (requestId === 1) slowStarted = true;
      await new Promise(resolve => setTimeout(resolve, slow ? 1000 : 20));
      await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ generated_at: String(requestId), overview: { latest_date: slow ? '2026-09-01' : requestId >= 4 ? '2026-09-24' : '2026-09-23' }, charts: { daily: [] } }) }).catch(() => {});
    });
    await racePage.route('**/api/rebuild-dashboard', route => route.fulfill({ contentType: 'application/json', body: '{}' }));
    await racePage.route('**/api/tables/sleep?*', async route => {
      tableStarted = true;
      await new Promise(resolve => setTimeout(resolve, 800));
      await route.fulfill({ contentType: 'application/json', body: '{"rows":[{"date":"2026-09-23","hours":8}],"meta":{"total":1}}' }).catch(() => {});
    });
    await racePage.goto(base, { waitUntil: 'domcontentloaded' });
    for (let i = 0; i < 100 && !slowStarted; i++) await racePage.waitForTimeout(20);
    assert.equal(slowStarted, true);
    await racePage.locator('#adminLoginBtn').click();
    await racePage.fill('#adminPasswordInput', 'synthetic-password');
    await racePage.locator('#adminLoginSubmit').click();
    await ready(racePage);
    await racePage.waitForTimeout(1100);
    assert.match(await racePage.locator('#latestRecordText').innerText(), /23/);
    assert.equal(await racePage.locator('#statsGrid > button').count(), 4);
    assert.match(await racePage.locator('.chart-empty:visible').first().innerText(), /暂无|没有/);
    check('late dashboard responses cannot overwrite data after login; empty datasets render safely');
    await racePage.locator('#reloadBtn').click();
    for (let i = 0; i < 100 && requests < 3; i++) await racePage.waitForTimeout(20);
    assert.equal(requests, 3);
    await racePage.locator('#reloadBtn').click();
    await ready(racePage);
    await racePage.waitForTimeout(1100);
    assert.match(await racePage.locator('#latestRecordText').innerText(), /24/);
    check('a second account refresh supersedes an earlier delayed response');
    await racePage.locator('#tab-sleep').click();
    for (let i = 0; i < 100 && !tableStarted; i++) await racePage.waitForTimeout(20);
    assert.equal(tableStarted, true);
    loggedOut = true;
    await racePage.locator('#adminLogoutBtn').click();
    await racePage.waitForFunction(() => document.querySelector('#dashboardNoticeText').textContent.includes('私有模式'));
    await racePage.waitForTimeout(900);
    assert.equal(await racePage.locator('[data-view-panel]:visible').count(), 0);
    assert.equal(await racePage.locator('#sleepTableWrap tbody tr').count(), 0);
    check('logout clears health records and rejects late table responses');
    await racePage.close();
    assert.deepEqual(errors, []);
  } finally {
    await browser.close();
    fs.writeFileSync(path.join(outputDir, 'browser-report.json'), JSON.stringify(report, null, 2));
  }
  console.log(JSON.stringify(report, null, 2));
}
main().catch(error => { console.error(error); process.exitCode = 1; });
