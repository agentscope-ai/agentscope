import { useQuery } from '@tanstack/react-query';

import { hubApi } from '@/api';

/**
 * The skill hubs registered on the server.
 *
 * Fixed at deploy time (`create_app(skill_hubs=[...])`) and cached for the
 * life of the tab, exactly like {@link useMCPHubs}. An empty list is the
 * normal state for a deployment that configured no hubs — not an error.
 */
export function useSkillHubs() {
	const { data, isPending, error, refetch } = useQuery({
		queryKey: ['skill-hubs'],
		queryFn: () => hubApi.skill.listHubs(),
		staleTime: Infinity,
	});

	return {
		hubs: data ?? [],
		loading: isPending,
		error: error as Error | null,
		refetch: () => void refetch(),
	};
}
