import { useState, useEffect, useCallback } from 'react';

import { sessionApi } from '../api';
import type { SessionRecord, CreateSessionRequest, UpdateSessionRequest } from '../api';

/**
 * Manages sessions for a given agent.
 * Clears and re-fetches whenever agentId changes.
 *
 * @param agentId - The agent whose sessions to load. Pass null to skip fetching.
 */
export function useSessions(agentId: string | null) {
	const [sessions, setSessions] = useState<SessionRecord[]>([]);
	const [loading, setLoading] = useState(false);
	const [error, setError] = useState<Error | null>(null);

	const refetch = useCallback(async () => {
		if (!agentId) {
			setSessions([]);
			return;
		}
		setLoading(true);
		setError(null);
		try {
			const res = await sessionApi.list(agentId);
			setSessions(res.sessions);
		} catch (e) {
			setError(e as Error);
		} finally {
			setLoading(false);
		}
	}, [agentId]);

	useEffect(() => {
		refetch();
	}, [refetch]);

	/** Creates a new session and refreshes the list. */
	const create = useCallback(
		async (body: CreateSessionRequest) => {
			const res = await sessionApi.create(body);
			await refetch();
			return res;
		},
		[refetch],
	);

	/** Updates a session's model config and refreshes the list. */
	const update = useCallback(
		async (sessionId: string, body: UpdateSessionRequest) => {
			if (!agentId) throw new Error('No agent selected');
			const res = await sessionApi.update(sessionId, agentId, body);
			await refetch();
			return res;
		},
		[agentId, refetch],
	);

	/** Deletes a session and refreshes the list. */
	const remove = useCallback(
		async (sessionId: string) => {
			if (!agentId) throw new Error('No agent selected');
			await sessionApi.delete(sessionId, agentId);
			await refetch();
		},
		[agentId, refetch],
	);

	return { sessions, loading, error, refetch, create, update, remove };
}
