import { useState, useEffect, useCallback, useRef } from 'react';

import { sessionApi } from '../api';
import type { SessionView, CreateSessionRequest, UpdateSessionRequest } from '../api';

/**
 * Manages session views for a given agent.
 *
 * Each entry is a `SessionView` (record + is_running + optional team
 * detail) — the same shape the backend returns. The hook clears and
 * re-fetches whenever agentId changes.
 *
 * @param agentId - The agent whose sessions to load. Pass null to skip fetching.
 * @returns Object with the loaded `sessions` array plus `loading` /
 *   `error` flags and `refetch` / `create` / `update` / `remove`
 *   helpers that all keep the local list in sync.
 */
export function useSessions(agentId: string | null) {
	const [state, setState] = useState<{
		agentId: string | null;
		sessions: SessionView[];
		loading: boolean;
		error: Error | null;
	}>({ agentId: null, sessions: [], loading: false, error: null });
	const currentAgentIdRef = useRef(agentId);
	const requestIdRef = useRef(0);
	const ownsState = state.agentId === agentId;
	const sessions = ownsState ? state.sessions : [];
	const loading = agentId !== null && (!ownsState || state.loading);
	const error = ownsState ? state.error : null;

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
		const requestedAgentId = agentId;
		if (currentAgentIdRef.current !== requestedAgentId) return [];

		const requestId = ++requestIdRef.current;
		const isCurrent = () =>
			requestId === requestIdRef.current && requestedAgentId === currentAgentIdRef.current;

		if (!requestedAgentId) {
			setState({ agentId: null, sessions: [], loading: false, error: null });
			return [];
		}
		setState((prev) => ({
			agentId: requestedAgentId,
			sessions: prev.agentId === requestedAgentId ? prev.sessions : [],
			loading: true,
			error: null,
		}));
		try {
			const res = await sessionApi.list(requestedAgentId);
			if (!isCurrent()) return [];
			setState({
				agentId: requestedAgentId,
				sessions: res.sessions,
				loading: false,
				error: null,
			});
			return res.sessions;
		} catch (e) {
			if (isCurrent()) {
				setState((prev) => ({
					agentId: requestedAgentId,
					sessions: prev.agentId === requestedAgentId ? prev.sessions : [],
					loading: false,
					error: e as Error,
				}));
			}
			return [];
		}
	}, [agentId]);

	useEffect(() => {
		currentAgentIdRef.current = agentId;
		refetch();
	}, [agentId, refetch]);

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
