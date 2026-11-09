import { useState, useEffect, useCallback } from 'react';

import { scheduleApi } from '../api';
import type { ScheduleRecord, CreateScheduleRequest, UpdateScheduleRequest } from '../api';

/**
 * Manages scheduled agent tasks with CRUD operations.
 * Fetches on mount and automatically re-fetches after each mutation.
 */
export function useSchedules() {
	const [schedules, setSchedules] = useState<ScheduleRecord[]>([]);
	const [loading, setLoading] = useState(false);
	const [error, setError] = useState<Error | null>(null);

	const refetch = useCallback(async () => {
		setLoading(true);
		setError(null);
		try {
			const res = await scheduleApi.list();
			setSchedules(res.schedules);
		} catch (e) {
			setError(e as Error);
		} finally {
			setLoading(false);
		}
	}, []);

	useEffect(() => {
		refetch();
	}, [refetch]);

	/** Creates a new schedule, registers it with the scheduler, and refreshes the list. */
	const create = useCallback(
		async (body: CreateScheduleRequest) => {
			const res = await scheduleApi.create(body);
			await refetch();
			return res;
		},
		[refetch],
	);

	/** Updates a schedule's config, reschedules the job, and refreshes the list. */
	const update = useCallback(
		async (scheduleId: string, body: UpdateScheduleRequest) => {
			const res = await scheduleApi.update(scheduleId, body);
			await refetch();
			return res;
		},
		[refetch],
	);

	/** Deletes a schedule, removes the job from the scheduler, and refreshes the list. */
	const remove = useCallback(
		async (scheduleId: string) => {
			await scheduleApi.delete(scheduleId);
			await refetch();
		},
		[refetch],
	);

	return { schedules, loading, error, refetch, create, update, remove };
}
