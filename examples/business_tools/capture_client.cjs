// Capture a single business request made by the original ERP client.
const fs = require('node:fs');
const { chromium } = require(process.env.BUSINESS_PLAYWRIGHT_MODULE);

function comparableDraftRequest(value) {
  if (Array.isArray(value)) return value.map(comparableDraftRequest);
  if (value === null || typeof value !== 'object') return value;
  // Each newly opened editor creates a different UI key. With no attachments
  // this key carries no business data, while the attachment list still compares.
  const emptyAttachments = Array.isArray(value.__attachmentItems) && !value.__attachmentItems.length;
  return Object.fromEntries(Object.entries(value)
    .filter(([key]) => key !== '__attachmentKey' || !emptyAttachments)
    .map(([key, item]) => [key, comparableDraftRequest(item)]));
}

async function capture({ origin, clientRoute, apiPattern, actions, storage, save, inspect, loginAcceptance }) {
  const browser = await chromium.launch({
    executablePath: process.env.BUSINESS_CHROMIUM,
    headless: true,
  });
  try {
    const context = await browser.newContext({
      serviceWorkers: 'block', viewport: { width: 1680, height: 1050 },
    });
    const page = await context.newPage();
    page.setDefaultTimeout(10000);
    await context.addInitScript(data => {
      for (const [key, value] of Object.entries(data)) localStorage.setItem(key, value);
    }, storage);
    let submit = false;
    let attempted = false;
    let requestBody;
    let write;
    let responseId;
    let onPreview;
    const preview = new Promise(resolve => { onPreview = resolve; });
    const detailPattern = save && new RegExp(
      '^/api' + save.detailRoute.replace(/\{\w+\}/, '\\d+') + '$'
    );
    await page.route('**/*', async route => {
      const request = route.request();
      const url = new URL(request.url());
      if (url.origin !== origin) return route.abort();
      if (request.method() === 'GET') return route.continue();
      if (!save || request.method() !== 'POST' ||
          url.pathname !== '/api' + save.saveRoute || !submit || attempted) return route.abort();
      attempted = true;
      requestBody = request.postDataJSON();
      if (!save.live) {
        onPreview({ preview: true });
        return route.abort();
      }
      try {
        const response = await route.fetch({ maxRedirects: 0 });
        write = await response.json();
        if (write.code === 200 && Number.isSafeInteger(write.data) && write.data > 0) {
          responseId = write.data;
        }
        return route.fulfill({ response });
      } catch {
        return route.abort();
      }
    });

    const api = new RegExp('^' + apiPattern + '$');
    const waitForBusiness = () => page.waitForResponse(response => {
      const url = new URL(response.url());
      return url.origin === origin && response.request().method() === 'GET' && api.test(url.pathname);
    });
    const navigate = () => page.goto(origin + '/#' + clientRoute, { waitUntil: 'domcontentloaded' });
    async function perform({ action, selector, value, button, text, first, state }) {
      const matches = button ? page.locator('button').filter({ hasText: button })
        : text ? page.getByText(text, { exact: true }) : page.locator(selector);
      const target = first ? matches.first() : matches;
      if (action === 'fill') await target.fill(value);
      else if (action === 'press') await target.press(value);
      else if (action === 'click') await target.click();
      else if (action === 'wait') await target.waitFor({ state: state || 'visible' });
      else throw new Error('Unsupported client action');
    }
    if (inspect) {
      await navigate();
      await page.locator('main, .erp-page-header, .layout-content').first().waitFor();
      for (const action of actions) await perform(action);
      return await page.evaluate(() => {
        const visible = element => Boolean(element.getClientRects().length);
        const dialogs = Array.from(document.querySelectorAll('[role=dialog]')).filter(visible);
        const root = dialogs.at(-1) || document.body;
        return {
          text: root.innerText.slice(0, 2600),
          options: Array.from(document.querySelectorAll(
            '.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option-content',
          )).filter(visible).map(option => option.innerText.trim()).slice(0, 30),
          controls: Array.from(root.querySelectorAll('button, input, textarea, [role=option]'))
            .filter(visible).sort((a, b) => Number(Boolean(a.disabled)) - Number(Boolean(b.disabled)))
            .slice(0, 60).map(element => ({
              tag: element.tagName.toLowerCase(),
              label: (element.innerText || element.getAttribute('aria-label') || '').trim().slice(0, 160),
              id: element.id || undefined,
              placeholder: element.placeholder || undefined,
              column: element.closest('[col-id]')?.getAttribute('col-id') || undefined,
              disabled: element.disabled || undefined,
              type: element.getAttribute('type') || undefined,
            })),
        };
      });
    }
    if (save) {
      await navigate();
      for (const action of actions.slice(0, -1)) await perform(action);
      const detail = page.waitForResponse(response => {
        const url = new URL(response.url());
        return responseId && url.origin === origin &&
          detailPattern.test(url.pathname) && url.pathname.endsWith('/' + responseId) &&
          response.request().method() === 'GET';
      }, { timeout: 15000 }).then(response => response.json()).catch(() => null);
      const issueLocator = page.locator(save.issueSelector).first();
      const issue = issueLocator.waitFor({ state: 'visible', timeout: 12000 })
        .then(() => issueLocator.innerText()).catch(() => '');
      submit = true;
      await perform(actions.at(-1));
      const outcome = await Promise.race([
        detail.then(value => ({ result: value })),
        issue.then(value => ({ issue: value.trim() })),
        preview,
      ]);
      if (outcome.issue === '' && responseId) outcome.result = await detail;
      return {
        status: outcome.preview ? 'preview' : outcome.result?.code === 200 ? 'saved'
          : responseId ? 'saved_needs_readback'
          : attempted ? 'save_attempted' : 'blocked',
        issue: outcome.issue || (responseId ? '' : write?.message || ''),
        attempted,
        request: comparableDraftRequest(requestBody),
        id: responseId,
        detail: outcome.result?.code === 200 ? outcome.result.data : undefined,
      };
    }
    let response;
    if (actions.length) {
      await navigate();
      for (const action of actions.slice(0, -1)) await perform(action);
      [response] = await Promise.all([waitForBusiness(), perform(actions.at(-1))]);
    } else {
      [response] = await Promise.all([waitForBusiness(), navigate()]);
    }
    const url = new URL(response.url());
    const query = Object.fromEntries(url.searchParams);
    if (Object.keys(query).length !== Array.from(url.searchParams.keys()).length) {
      throw new Error('Request has repeated query keys');
    }
    const authCases = [];
    if (loginAcceptance) {
      const cases = [
        { name: 'HTTP 401', status: 401, body: { message: '登录已过期' }, expectedRedirect: true },
        { name: '响应体 401', status: 200, body: { code: 401, message: '登录已过期' }, expectedRedirect: true },
        { name: '权限不足', status: 200, body: { code: 403, message: '权限不足' }, expectedRedirect: false },
        { name: '业务失败', status: 200, body: { code: 500, message: '普通业务错误' }, expectedRedirect: false },
      ];
      for (const scenario of cases) {
        const testPage = await context.newPage();
        const pattern = url.pathname;
        try {
          await testPage.route('**/*', route => {
            const request = route.request();
            const target = new URL(request.url());
            return target.origin === origin && request.method() === 'GET'
              ? route.continue() : route.abort();
          });
          const matchesQuery = value => {
            const candidate = new URL(value);
            return candidate.origin === origin && candidate.pathname === pattern;
          };
          await testPage.route(matchesQuery, route => route.fulfill({
            status: scenario.status, json: scenario.body,
          }));
          const observed = testPage.waitForResponse(
            result => matchesQuery(result.url()) && result.status() === scenario.status,
            { timeout: 10000 },
          );
          await Promise.all([
            observed,
            testPage.goto(origin + '/#' + clientRoute, { waitUntil: 'domcontentloaded' }),
          ]).catch(() => {
            throw new Error('Original client did not send the protected request for ' + scenario.name);
          });
          const redirected = await testPage.waitForURL(
            value => value.hash.startsWith('#/login'), { timeout: 1800 },
          ).then(() => true, error => {
            if (error.name !== 'TimeoutError') throw error;
            return false;
          });
          authCases.push({ ...scenario, redirected });
        } finally {
          await testPage.close();
        }
      }
    }
    return {
      request: { method: 'GET', path: url.pathname.replace(/^\/api(?=\/)/, ''), query },
      status: response.status(),
      response: await response.json(),
      ...(loginAcceptance ? { authCases } : {}),
    };
  } finally {
    await browser.close();
  }
}

capture(JSON.parse(fs.readFileSync(0, 'utf8'))).then(
  result => process.stdout.write(JSON.stringify(result)),
  error => {
    process.stderr.write(error.message);
    process.exitCode = 1;
  },
);
