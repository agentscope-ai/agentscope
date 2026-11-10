import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useSyncExternalStore } from 'react';

import { backgroundTaskApi } from '@/api';
import { getBaseUrl, getUserId } from '@/api/client';

function subscribeVisibility(onChange: () => void) {
	document.addEventListener('visibilitychange', onChange);
	return () => document.removeEventListener('visibilitychange', onChange);
}

const isPageVisible = () => document.visibilityState === 'visible';

/** Poll the selected session even when its last snapshot has no running tasks. */
export function useBackgroundTasks(sessionId: string | null, open: boolean) {
	const queryClient = useQueryClient();
	const visible = useSyncExternalStore(subscribeVisibility, isPageVisible);
	const baseUrl = getBaseUrl();
	const userId = getUserId();
	const queryKey = useMemo(
		() => ['background-tasks', baseUrl, userId, sessionId],
		[baseUrl, userId, sessionId],
	);
	const active = open && visible && sessionId !== null;
	const query = useQuery({
		queryKey,
		queryFn: ({ signal }) => backgroundTaskApi.list(sessionId!, baseUrl, userId, signal),
		enabled: active,
		staleTime: 0,
		gcTime: 0,
		retry: false,
		refetchInterval: active ? 3000 : false,
		refetchIntervalInBackground: false,
		refetchOnWindowFocus: true,
	});

	// Disabling a query stops its timer but does not abort an in-flight fetch.
	useEffect(() => {
		if (!active) void queryClient.cancelQueries({ queryKey, exact: true });
	}, [active, queryClient, queryKey]);

	useEffect(
		() => () => {
			void queryClient.cancelQueries({ queryKey, exact: true });
		},
		[queryClient, queryKey],
	);

	return { ...query, visible };
}
