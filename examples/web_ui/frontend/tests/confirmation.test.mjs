import assert from 'node:assert/strict';
import test from 'node:test';

import { chromium } from 'playwright-core';
import { createServer } from 'vite';

test('keyboard confirmation acts only on the focused decision', async (t) => {
	const server = await createServer({ server: { host: '127.0.0.1', port: 0 } });
	t.after(() => server.close());
	await server.listen();
	const browser = await chromium.launch();
	t.after(() => browser.close());
	const page = await browser.newPage();
	await page.goto(new URL('tests/confirmation.html', server.resolvedUrls.local[0]).href);
	const buttons = page.getByRole('button');
	await buttons.first().waitFor();
	const chat = page.getByRole('textbox');
	await chat.focus();
	await chat.press('Shift+Enter');
	await chat.dispatchEvent('keydown', { key: 'Enter', isComposing: true, bubbles: true });
	await chat.press('Enter');
	assert.deepEqual(await page.evaluate(() => window.decisions), []);

	await buttons.first().focus();
	await buttons.first().press('Shift+Enter');
	await buttons.first().dispatchEvent('keydown', { key: 'Enter', isComposing: true });
	assert.deepEqual(await page.evaluate(() => window.decisions), []);
	await page.keyboard.press('ArrowDown');
	await page.keyboard.press('Enter');
	assert.deepEqual(await page.evaluate(() => window.decisions), [['create_ticket', false]]);
	await buttons.nth(2).focus();
	await page.keyboard.press('Enter');
	assert.deepEqual(await page.evaluate(() => window.decisions), [
		['create_ticket', false],
		['add_contact', true],
	]);
});
