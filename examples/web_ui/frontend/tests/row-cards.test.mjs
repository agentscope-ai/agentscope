import assert from 'node:assert/strict';
import test from 'node:test';

import { chromium } from 'playwright-core';

// The Python acceptance runner starts the isolated service and builds the fixtures.
const url = process.env.ROW_CARD_TEST_URL;
for (const [device, width] of [
	['desktop', 1280],
	['mobile', 390],
]) {
	for (const [system, title] of [
		['purchase_receipts', '采购入库单'],
		['tickets', '工单'],
	]) {
		test(`${device} · ${title} opens and replies to the original second row`, async (t) => {
			assert.ok(url, 'Run tests/run_row_card_browser.py against a built frontend');
			const browser = await chromium.launch();
			t.after(() => browser.close());
			const page = await browser.newPage({
				viewport: { width, height: 844 },
				hasTouch: device === 'mobile',
				isMobile: device === 'mobile',
				locale: 'zh-CN',
			});
			page.on('pageerror', (err) => t.diagnostic(err.message));
			await page.addInitScript((base) => {
				localStorage.setItem('server_url', base);
				localStorage.setItem('username', 'reader');
				localStorage.setItem('i18nextLng', 'zh');
				localStorage.setItem('chat_tour_done', '1');
			}, url);
			await page.goto(`${url}/chat/reader/${system}-${device}`);
			const cards = page.getByRole('region', { name: title, exact: true });
			for (let i = 0; i < 2; i++) {
				const composer = page.locator('textarea:visible');
				await composer.fill('查询最新两张');
				if (device === 'mobile')
					await page.getByRole('button', { name: '发送', exact: true }).tap();
				else await composer.press('Enter');
				try {
					await cards
						.nth(i)
						.getByRole('button', { name: '回复这张卡片' })
						.waitFor({ timeout: 15000 });
				} catch (error) {
					t.diagnostic(await page.locator('body').innerText());
					throw error;
				}
			}
			// Rehydrate the original tool result and its call ID from session history.
			await page.reload();
			await cards.nth(1).waitFor();
			const old = cards.first();
			const row = old.getByRole('button').nth(1);
			const bounds = await row.boundingBox();
			assert.ok(bounds && bounds.x >= 0 && bounds.x + bounds.width <= width + 1);
			if (device === 'mobile') await row.tap();
			else await row.click();
			const details = page
				.locator('[data-role="assistant"] code')
				.filter({ hasText: 'detail-2' });
			await details.first().waitFor();
			await old.getByRole('button', { name: '回复这张卡片' }).click();
			await old.getByRole('textbox').fill('打开第二张');
			await old.getByRole('button', { name: '发送', exact: true }).click();
			await details.nth(1).waitFor();
			assert.equal(await page.getByText('回复列表卡片', { exact: true }).count(), 2);
		});
	}
}
