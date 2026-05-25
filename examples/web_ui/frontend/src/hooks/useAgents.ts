import { useState, useEffect, useCallback } from 'react';

import { agentApi } from '../api';
import type { AgentRecord, CreateAgentRequest, UpdateAgentRequest } from '../api';

/**
 * Manages the full agent list with CRUD operations.
 * Fetches on mount and automatically re-fetches after each mutation.
 */
export function useAgents() {
	const [agents, setAgents] = useState<AgentRecord[]>([]);
	const [loading, setLoading] = useState(false);
	const [error, setError] = useState<Error | null>(null);

	const refetch = useCallback(async () => {
		setLoading(true);
		setError(null);
		try {
			const res = await agentApi.list();
			setAgents(res.agents);
		} catch (e) {
			setError(e as Error);
		} finally {
			setLoading(false);
		}
	}, []);

	useEffect(() => {
		refetch();
	}, [refetch]);

	/** Creates a new agent and refreshes the list. */
	const create = useCallback(
		async (body: CreateAgentRequest) => {
			const res = await agentApi.create(body);
			await refetch();
			return res;
		},
		[refetch],
	);

	/** Partially updates an agent and refreshes the list. */
	const update = useCallback(
		async (agentId: string, body: UpdateAgentRequest) => {
			const res = await agentApi.update(agentId, body);
			await refetch();
			return res;
		},
		[refetch],
	);

	/** Deletes an agent and refreshes the list. */
	const remove = useCallback(
		async (agentId: string) => {
			await agentApi.delete(agentId);
			await refetch();
		},
		[refetch],
	);

	return { agents, loading, error, refetch, create, update, remove };
}
