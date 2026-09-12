import { useQuery } from '@tanstack/react-query';

import { agentApi } from '@/api';

/**
 * The full `AgentData` JSON Schema backing the agent form.
 *
 * Derived from Pydantic models on the backend, so it cannot change while
 * the server is up: fetched once and served from cache for every create /
 * edit dialog afterwards.
 */
export function useAgentSchema() {
	const { data, isPending, error } = useQuery({
		queryKey: ['agent-schema'],
		queryFn: () => agentApi.getSchema(),
		staleTime: Infinity,
	});

	return { schema: data ?? null, loading: isPending, error: error as Error | null };
}
