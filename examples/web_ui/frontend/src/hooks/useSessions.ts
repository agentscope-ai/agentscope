import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback } from 'react';

import { sessionApi } from '../api';
import type { SessionView, CreateSessionRequest, UpdateSessionRequest } from '../api';

/** Stable empty list, so `sessions` keeps its identity between renders. */
const NO_SESSIONS: SessionView[] = [];

/** Every agent's session list, so one mutation refreshes all of them. */
const SESSIONS_KEY = ['sessions'];

/**
 * Manages session views for a given agent.
 *
 * Each entry is a `SessionView` (record + is_running + optional team
 * detail) — the same shape the backend returns.
 *
 * Backed by the shared query cache, keyed on `agentId`. The chat page
 * mounts this hook twice — once for the outer sidebar's list, once
 * inside the viewport — and both now read the same entry, so a rename
 * from the sidebar reaches the viewport's header without either side
 * knowing about the other. Drilling into a team member gives the
 * viewport a different `agentId`, hence its own entry, which is what
 * that view wants.
 *
 * @param agentId - The agent whose sessions to load. Pass null to skip fetching.
 * @returns Object with the loaded `sessions` array plus `loading` /
 *   `error` flags and `refetch` / `create` / `update` / `remove`
 *   helpers that all keep the list in sync.
 */
export function useSessions(agentId: string | null) {
	const queryClient = useQueryClient();

	const {
		data,
		isPending,
		error,
		refetch: runRefetch,
	} = useQuery({
		queryKey: [...SESSIONS_KEY, agentId],
		queryFn: () => sessionApi.list(agentId as string).then((res) => res.sessions),
		enabled: agentId !== null,
		// This list carries `is_running` and team membership, both of
		// which move underneath the page. Sharing one copy between the
		// two mounts is the point here, not skipping the read — so a
		// view that needs it still re-reads it when it mounts.
		staleTime: 0,
	});

	/** Drop every agent's list, so both mounts re-read after a write. */
	const invalidate = useCallback(
		() => queryClient.invalidateQueries({ queryKey: SESSIONS_KEY }),
		[queryClient],
	);

	/**
	 * Reload the session list.
	 *
	 * Also returns the fresh list, so a caller reacting to an event can
	 * act on it immediately — reading the `sessions` state right after
	 * awaiting would still see the pre-update value from its closure.
	 *
	 * @returns The reloaded views, or an empty array when there is no
	 *   agent or the request failed.
	 */
	const refetch = useCallback(async (): Promise<SessionView[]> => {
		const result = await runRefetch();
		return result.data ?? NO_SESSIONS;
	}, [runRefetch]);

	/** Creates a new session and refreshes the list. */
	const create = useCallback(
		async (body: CreateSessionRequest) => {
			const res = await sessionApi.create(body);
			await invalidate();
			return res;
		},
		[invalidate],
	);

	/** Updates a session's model config and refreshes the list. */
	const update = useCallback(
		async (sessionId: string, body: UpdateSessionRequest) => {
			if (!agentId) throw new Error('No agent selected');
			const res = await sessionApi.update(sessionId, agentId, body);
			await invalidate();
			return res;
		},
		[agentId, invalidate],
	);

	/** Deletes a session and refreshes the list. */
	const remove = useCallback(
		async (sessionId: string) => {
			if (!agentId) throw new Error('No agent selected');
			await sessionApi.delete(sessionId, agentId);
			await invalidate();
		},
		[agentId, invalidate],
	);

	return {
		sessions: data ?? NO_SESSIONS,
		loading: agentId !== null && isPending,
		error: error as Error | null,
		refetch,
		create,
		update,
		remove,
	};
}
