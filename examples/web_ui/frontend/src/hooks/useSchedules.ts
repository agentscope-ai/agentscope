import { useState, useEffect, useCallback, useRef } from 'react';
import { toast } from 'sonner';

import { scheduleApi } from '../api';
import type { ScheduleRecord, CreateScheduleRequest, UpdateScheduleRequest } from '../api';
import { ApiError } from '../api/client';

/**
 * Manages scheduled agent tasks with CRUD operations.
 * Fetches on mount and automatically re-fetches after each mutation.
 */
export function useSchedules() {
	const [schedules, setSchedules] = useState<ScheduleRecord[]>([]);
	const [initialLoading, setInitialLoading] = useState(true);
	const [error, setError] = useState<Error | null>(null);
	const requestSeq = useRef(0);
	const mounted = useRef(false);

	const refetch = useCallback(async () => {
		if (!mounted.current) return;
		const seq = ++requestSeq.current;
		setError(null);
		try {
			const res = await scheduleApi.list();
			if (seq === requestSeq.current) setSchedules(res.schedules);
		} catch (e) {
			if (seq === requestSeq.current) {
				setError(e as Error);
				if (e instanceof ApiError) toast.error(e.detail);
			}
		} finally {
			if (seq === requestSeq.current) setInitialLoading(false);
		}
	}, []);

	useEffect(() => {
		mounted.current = true;
		void refetch();
		return () => {
			mounted.current = false;
			requestSeq.current += 1;
		};
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
			if (!mounted.current) return res;
			setSchedules((current) => current.map((item) => (item.id === res.id ? res : item)));
			void refetch();
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

	return { schedules, initialLoading, error, refetch, create, update, remove };
}
