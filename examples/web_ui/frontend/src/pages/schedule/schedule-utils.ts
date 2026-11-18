import { CronDate } from 'cron-parser';
import { format } from 'date-fns';

/** Convert picker date/time in the task's zone, rejecting gaps and choosing the first fold. */
export function getScheduleStartAt(date: Date, time: string, timezone: string): string {
	if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(time)) {
		throw new Error('Invalid start time. Please enter a time in HH:mm format.');
	}
	const local = `${format(date, 'yyyy-MM-dd')}T${time}:00.000`;
	const parsed = new CronDate(local, timezone);
	const wallTime = Date.parse(`${local}Z`);
	// Sample both sides of a transition, including non-hour offset changes.
	// Date objects prevent CronDate from treating timestamp zero as missing.
	const day = 24 * 60 * 60 * 1000;
	const offsets = new Set(
		[-day, 0, day].map((delta) =>
			new CronDate(new Date(parsed.getTime() + delta), timezone).getUTCOffset(),
		),
	);
	const candidates = [...offsets]
		.map((offset) => new CronDate(new Date(wallTime - offset * 60 * 1000), timezone))
		.filter(
			(candidate) => candidate.getTime() + candidate.getUTCOffset() * 60 * 1000 === wallTime,
		);
	if (!candidates.length) {
		throw new Error(
			'The selected start time does not exist in the task time zone. Please choose another time.',
		);
	}
	return new Date(Math.min(...candidates.map((candidate) => candidate.getTime()))).toISOString();
}

/** Convert a picker date to the inclusive end of that day in the task's timezone. */
export function getScheduleEndAt(date: Date | undefined, timezone: string): string | undefined {
	if (!date) return undefined;
	// Preserve the calendar date selected in the browser, not its midnight instant.
	const end = new CronDate(format(date, 'yyyy-MM-dd'), timezone);
	end.setEndOfDay();
	return end.toDate().toISOString();
}

export interface ParsedSchedule {
	frequency: 'daily' | 'weekly' | 'monthly' | 'once' | 'custom';
	time: string; // HH:mm format
	weekday?: number; // 0-6 for weekly
	dayOfMonth?: number; // 1-31 for monthly
	date?: Date; // for once
}

export function parseCronExpression(cronExpression: string, startedAt: string): ParsedSchedule {
	const parts = cronExpression.trim().split(/\s+/);
	if (parts.length !== 5) {
		return { frequency: 'custom', time: '00:00' };
	}

	const [minute, hour, day, month, weekday] = parts;
	const time = `${hour.padStart(2, '0')}:${minute.padStart(2, '0')}`;

	if (day === '*' && month === '*' && weekday === '*') {
		return { frequency: 'daily', time };
	}

	if (day === '*' && month === '*' && weekday !== '*') {
		return { frequency: 'weekly', time, weekday: parseInt(weekday) };
	}

	if (day !== '*' && month === '*' && weekday === '*') {
		return { frequency: 'monthly', time, dayOfMonth: parseInt(day) };
	}

	if (day !== '*' && month !== '*') {
		return { frequency: 'once', time, date: new Date(startedAt) };
	}

	return { frequency: 'custom', time };
}

export function getFrequencyLabel(parsed: ParsedSchedule, t: (key: string) => string): string {
	switch (parsed.frequency) {
		case 'daily':
			return t('schedule.freqDaily');
		case 'weekly':
			return t('schedule.freqWeekly');
		case 'monthly':
			return t('schedule.freqMonthly');
		case 'once':
			return t('schedule.freqOnce');
		default:
			return 'Custom';
	}
}
