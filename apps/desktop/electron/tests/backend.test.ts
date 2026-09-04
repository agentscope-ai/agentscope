import assert from 'node:assert/strict';
import { once } from 'node:events';
import { Writable } from 'node:stream';
import test from 'node:test';

import {
	BACKEND_PORT_PREFIX,
	DESKTOP_SHUTDOWN_COMMAND,
	buildDesktopCsp,
	getBundledExecutableName,
	parseBackendPort,
	requestGracefulShutdown,
} from '../backend';

test('parseBackendPort accepts only a complete valid handshake', () => {
	const cases = [
		`${BACKEND_PORT_PREFIX}43123`,
		`  ${BACKEND_PORT_PREFIX}1  `,
		`${BACKEND_PORT_PREFIX}65535`,
		`${BACKEND_PORT_PREFIX}0`,
		`${BACKEND_PORT_PREFIX}65536`,
		`${BACKEND_PORT_PREFIX}12x`,
		'backend log line',
	];
	assert.deepEqual(cases.map(parseBackendPort), [43123, 1, 65535, null, null, null, null]);
});

test('getBundledExecutableName maps every supported platform', () => {
	assert.deepEqual(
		(['darwin', 'linux', 'win32'] as NodeJS.Platform[]).map((platform) => [
			platform,
			getBundledExecutableName(platform),
		]),
		[
			['darwin', 'agentscope-backend'],
			['linux', 'agentscope-backend'],
			['win32', 'agentscope-backend.exe'],
		],
	);
});

test('buildDesktopCsp allows only the selected loopback port', () => {
	assert.equal(
		buildDesktopCsp('http://127.0.0.1:43123'),
		[
			"default-src 'self'",
			"base-uri 'none'",
			"object-src 'none'",
			"form-action 'none'",
			"script-src 'self'",
			"style-src 'self' 'unsafe-inline'",
			"font-src 'self' data:",
			"img-src 'self' data: blob: https: http://127.0.0.1:43123",
			"media-src 'self' data: blob: https: http://127.0.0.1:43123",
			"connect-src 'self' http://127.0.0.1:43123",
			'frame-src blob: http://127.0.0.1:43123',
			"worker-src 'self' blob:",
		].join('; '),
	);
	assert.throws(
		() => buildDesktopCsp('http://localhost:43123'),
		/Desktop backend URL must use a dynamic loopback port/,
	);
});

test('requestGracefulShutdown writes one shutdown command', async () => {
	const chunks: Buffer[] = [];
	const stdin = new Writable({
		write(chunk, _encoding, callback) {
			chunks.push(Buffer.from(chunk));
			callback();
		},
	});

	assert.equal(requestGracefulShutdown(stdin), true);
	await once(stdin, 'finish');
	assert.equal(Buffer.concat(chunks).toString(), `${DESKTOP_SHUTDOWN_COMMAND}\n`);
	assert.equal(requestGracefulShutdown(stdin), false);
	assert.equal(requestGracefulShutdown(null), false);
});
