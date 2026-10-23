const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const outputDir = process.env.TEST_OUTPUT_DIR || 'test-results';
fs.mkdirSync(outputDir, { recursive: true });
const base = process.env.BASE_URL || 'http://127.0.0.1:9001';
const report = { base, layouts: [], checks: [] };
const viewNames = ['overview', 'sleep', 'activity', 'recovery', 'body', 'account'];
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
      assert.equal(await page.locator('#statsGrid > button').count(), 6);
      const initialRequests = await page.evaluate(() => performance.getEntriesByType('resource').map(entry => entry.name));
      assert.equal(initialRequests.some(url => url.includes('/api/profile-summaries')), false);
      assert.equal(initialRequests.some(url => url.includes('/api/tables/')), false);
      assert.equal(await page.locator('#profileSelect, #tab-family, #panel-family, #existingProfilesList, #newProfileName').count(), 0);
      assert.equal(await page.locator('[data-view]').count(), 6);
      assert.match(await page.title(), /Fitflare/);
      assert.equal(initialRequests.some(url => /\/api\/(profiles|profile-summaries|dashboard\/)/.test(url)), false);
      check(`${name}: six personal views; no profile controls or legacy profile requests; homepage defers record tables`);
      const initial = await layout(page);
      await page.screenshot({ path: path.join(outputDir, `after-${name}.png`), fullPage: true });
      const chartId = await page.evaluate(() => window.Chart.getChart('overviewStepsChart')?.id);
      for (const range of ['14', '90', '30']) {
        await page.selectOption('#rangeSelect', range);
        await page.waitForTimeout(150);
        assert.equal(await page.evaluate(() => window.Chart.getChart('overviewStepsChart')?.id), chartId);
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
      check(`${name}: all six views render without overflow`);
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
      assert.equal(await page.locator('dialog, input[type=password], #adminLoginBtn, #openAccountBtn, #syncBtn').count(), 0);
      assert.equal(initialRequests.some(url => /\/api\/(admin|authorize|fetch-)/.test(url)), false);
      check(`${name}: no website login, management forms or management requests`);
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
    await page.goto(`${base}/?view=lifestyle`, { waitUntil: 'networkidle' });
    await ready(page);
    assert.equal(await page.locator('#panel-body').isVisible(), true);
    check('legacy lifestyle links open the body and nutrition view');
    await page.goto(`${base}/?view=recovery&range=14`, { waitUntil: 'networkidle' });
    await ready(page);
    for (const path of ['/.env', '/server.py', '/profiles/index.json', '/js/not-public.txt', '/script.js', '/ui-cn.js']) {
      const response = await page.request.get(base + path);
      assert.equal(response.status(), 404, path);
    }
    assert.match((await page.request.get(base + '/')).headers()['cache-control'], /no-cache/);
    assert.match((await page.request.get(base + '/vendor/chart.umd-4.4.1.min.js')).headers()['cache-control'], /immutable/);
    assert.match((await page.request.get(base + '/api/dashboard')).headers()['cache-control'], /no-store/);
    assert.equal((await page.request.post(base + '/api/account/setup', { data: {} })).status(), 405);
    check('static allowlist, asset caching and anonymous write protection');
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
    await page.route('**/api/account', route => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ profile_id: 'Demo', has_data: false }) }));
    await page.reload({ waitUntil: 'networkidle' });
    await ready(page);
    assert.equal(await page.locator('#dashboardNotice').isVisible(), true);
    assert.equal(await page.locator('#overviewMetrics').isVisible(), false);
    assert.match(await page.locator('#dashboardNoticeText').innerText(), /暂无健康记录/);
    assert.equal(await page.locator('dialog, input').count(), 0);
    check('new installation shows a read-only empty state');
    await page.close();

    const racePage = await browser.newPage();
    racePage.on('pageerror', error => errors.push(error.message));
    let requests = 0, slowStarted = false, tableStarted = false, hasData = true;
    await racePage.route('**/api/account', route => route.fulfill({ contentType: 'application/json', body: JSON.stringify({profile_id:'Demo', has_data:hasData}) }));
    await racePage.route('**/api/dashboard?*', async route => {
      const id = ++requests;
      const slow = id === 1 || id === 3;
      if (id === 1) slowStarted = true;
      await new Promise(resolve => setTimeout(resolve, slow ? 1000 : 20));
      await route.fulfill({ contentType:'application/json', body:JSON.stringify({generated_at:String(id),overview:{latest_date:slow?'2026-09-01':id>=4?'2026-09-24':'2026-09-23'},charts:{daily:[]}}) }).catch(()=>{});
    });
    await racePage.route('**/api/tables/sleep?*', async route => {
      tableStarted = true;
      await new Promise(resolve=>setTimeout(resolve,800));
      await route.fulfill({contentType:'application/json',body:'{"rows":[{"date":"2026-09-23","hours":8}],"meta":{"total":1}}'}).catch(()=>{});
    });
    await racePage.goto(base,{waitUntil:'domcontentloaded'});
    for (let i=0;i<100&&!slowStarted;i++) await racePage.waitForTimeout(20);
    assert.equal(slowStarted,true);
    await racePage.locator('#reloadBtn').click();
    await ready(racePage);
    await racePage.waitForTimeout(1100);
    assert.match(await racePage.locator('#latestRecordText').innerText(),/23/);
    assert.match(await racePage.locator('.chart-empty:visible').first().innerText(),/暂无|没有/);
    check('refresh supersedes delayed dashboard responses; empty chart data renders safely');
    await racePage.locator('#reloadBtn').click();
    for(let i=0;i<100&&requests<3;i++) await racePage.waitForTimeout(20);
    assert.equal(requests,3);
    await racePage.locator('#reloadBtn').click();
    await ready(racePage);
    await racePage.waitForTimeout(1100);
    assert.match(await racePage.locator('#latestRecordText').innerText(),/24/);
    await racePage.locator('#tab-sleep').click();
    await racePage.locator('#sleepTableWrap').evaluate(element=>{element.closest('details').open=true;});
    for(let i=0;i<100&&!tableStarted;i++) await racePage.waitForTimeout(20);
    assert.equal(tableStarted,true);
    hasData=false;
    await racePage.locator('#reloadBtn').click();
    await ready(racePage);
    await racePage.waitForTimeout(900);
    assert.equal(await racePage.locator('[data-view-panel]:visible').count(),0);
    assert.equal(await racePage.locator('#sleepTableWrap tbody tr').count(),0);
    check('a newer empty-data response clears records and rejects late table responses');
    await racePage.close();
    assert.deepEqual(errors, []);
  } finally {
    await browser.close();
    fs.writeFileSync(path.join(outputDir, 'browser-report.json'), JSON.stringify(report, null, 2));
  }
  console.log(JSON.stringify(report, null, 2));
}
main().catch(error => { console.error(error); process.exitCode = 1; });
