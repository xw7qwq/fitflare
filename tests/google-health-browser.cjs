// Synthetic authorization UI flow. Google requests never leave this browser.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const base = process.env.BASE_URL;

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(base, { waitUntil: 'networkidle' });
    await page.click('#adminLoginBtn');
    await page.fill('#adminPasswordInput', process.env.PREVIEW_ADMIN_PASSWORD);
    await page.click('#adminLoginSubmit');
    await page.waitForSelector('#adminLogoutBtn:not(.hidden)');
    await page.click('#openAccountBtn');
    await page.waitForSelector('#accountModal[open]');
    assert.equal(await page.getAttribute('#clientSecret', 'type'), 'password');
    assert.equal(await page.inputValue('#redirectUri'), 'http://localhost:8080/callback');
    let saved;
    await page.route('**/api/account/setup', async route => {
      saved = route.request().postDataJSON();
      assert.ok(route.request().headers()['x-fitflare-csrf']);
      await route.fulfill({ json: { message: 'Configured', profile_id: 'Demo', configured: true, authorized: false, has_data: true } });
    });
    await page.route('**/api/authorize', route => route.fulfill({ json: {
      mode: 'manual', provider: 'google',
      auth_url: 'https://accounts.google.com/o/oauth2/v2/auth?state=synthetic-state',
      redirect_uri: 'http://localhost:8080/callback',
    } }));
    await page.fill('#clientId', 'synthetic.apps.googleusercontent.com');
    await page.fill('#clientSecret', 'synthetic-client-secret');
    await page.click('#accountSetupSubmit');
    await page.waitForSelector('#authModal[open]');
    assert.equal(saved.profileName, undefined);
    assert.equal(saved.clientId, 'synthetic.apps.googleusercontent.com');
    assert.equal(saved.redirectUri, 'http://localhost:8080/callback');
    assert.equal(await page.inputValue('#clientSecret'), '');
    assert.match(await page.getAttribute('#authOpenLink', 'href'), /^https:\/\/accounts.google.com\//);
    let submitted;
    await page.route('**/api/authorize-exchange', async route => {
      submitted = route.request().postDataJSON();
      await route.fulfill({ status: 400, json: { code: 'account_mismatch', error: '账号与本地记录不一致' } });
    });
    const callback = 'http://localhost:8080/callback?code=synthetic-code&state=synthetic-state';
    await page.fill('#authRedirectValue', callback);
    await page.click('#authExchangeSubmit');
    await page.waitForFunction(() => document.querySelector('#toast').textContent.includes('账号与本地记录不一致'));
    assert.deepEqual(submitted, { redirectUrl: callback });
    assert.equal(await page.isVisible('#authModal'), true);
    await page.unroute('**/api/authorize-exchange');
    await page.route('**/api/authorize-exchange', route => route.fulfill({ json: {
      message: 'Connected', warning: '部分数据权限未授予，对应指标暂不更新。',
    } }));
    await page.fill('#authRedirectValue', callback);
    await page.click('#authExchangeSubmit');
    await page.waitForFunction(() => !document.querySelector('#authModal').open);
    await page.waitForFunction(() => document.querySelector('#toast').textContent.includes('部分数据权限未授予'));
    assert.equal(await page.inputValue('#authRedirectValue'), '');
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    assert.deepEqual(errors, []);
    console.log('Google Health browser checks passed: configuration, state-preserving callback, account errors, partial consent, secret clearing.');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
