import assert from 'node:assert/strict';
import { afterEach, test } from 'node:test';

import { getScheduleEndAt } from '../src/pages/schedule/schedule-utils.ts';

const originalTimezone = process.env.TZ;

afterEach(() => {
	if (originalTimezone === undefined) delete process.env.TZ;
	else process.env.TZ = originalTimezone;
});

test('preserves the selected date across browser timezones', () => {
	for (const browserTimezone of ['UTC', 'Asia/Shanghai', 'America/Los_Angeles']) {
		process.env.TZ = browserTimezone;
		const selected = new Date(2026, 9, 31);
		const originalTime = selected.getTime();
		assert.equal(
			getScheduleEndAt(selected, 'Asia/Shanghai'),
			'2026-10-31T15:59:59.999Z',
			browserTimezone,
		);
		assert.equal(selected.getTime(), originalTime, 'must not mutate the picker date');
	}
});

test('uses the offset at the end of a daylight-saving transition day', () => {
	process.env.TZ = 'Asia/Shanghai';
	assert.equal(
		getScheduleEndAt(new Date(2026, 2, 8), 'America/New_York'),
		'2026-03-09T03:59:59.999Z',
	);
	assert.equal(
		getScheduleEndAt(new Date(2026, 10, 1), 'America/New_York'),
		'2026-11-02T04:59:59.999Z',
	);
});

test('handles fractional offsets and year boundaries', () => {
	process.env.TZ = 'America/Los_Angeles';
	assert.equal(
		getScheduleEndAt(new Date(2026, 11, 31), 'Asia/Kathmandu'),
		'2026-12-31T18:14:59.999Z',
	);
	assert.equal(
		getScheduleEndAt(new Date(2026, 11, 31), 'America/New_York'),
		'2027-01-01T04:59:59.999Z',
	);
});

test('rejects an invalid date or timezone rather than submitting a wrong boundary', () => {
	assert.throws(() => getScheduleEndAt(new Date('invalid'), 'UTC'));
	assert.throws(() => getScheduleEndAt(new Date(2026, 9, 31), 'Mars/Olympus_Mons'));
});
