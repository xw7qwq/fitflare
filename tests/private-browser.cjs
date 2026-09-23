const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const base = process.env.BASE_URL;
async function main() {
  const browser = await chromium.launch({ headless: true, ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH } : {}) });
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(base, { waitUntil: 'networkidle' });
    assert.match(await page.locator('#dashboardNoticeText').innerText(), /私有模式/);
    assert.equal(await page.locator('#overviewMetrics').isVisible(), false);
    assert.equal(await page.locator('#profileSelect, #tab-family, #existingProfilesList, #newProfileName').count(), 0);
    await page.locator('#adminLoginBtn').click();
    await page.fill('#adminPasswordInput', process.env.PREVIEW_ADMIN_PASSWORD);
    await page.locator('#adminLoginSubmit').click();
    await page.waitForSelector('#dashboardNotice', { state: 'hidden' });
    assert.equal(await page.locator('#statsGrid > button').count(), 4);
    await page.locator('#tab-sleep').click();
    await page.waitForSelector('#sleepTableWrap tbody tr');
    await page.locator('#adminLogoutBtn').click();
    await page.waitForFunction(() => document.querySelector('#dashboardNoticeText').textContent.includes('私有模式'));
    assert.equal(await page.locator('#overviewMetrics').isVisible(), false);
    assert.equal(await page.locator('[data-view-panel]:visible').count(), 0);
    assert.equal(await page.locator('#sleepTableWrap tbody tr').count(), 0);
    assert.deepEqual(errors, []);
    console.log('Private mode: anonymous blocked; login loads data; logout removes visible data.');
  } finally { await browser.close(); }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
