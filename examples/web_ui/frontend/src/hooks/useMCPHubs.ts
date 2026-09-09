import { useQuery } from '@tanstack/react-query';

import { hubApi } from '@/api';

/**
 * The MCP hubs registered on the server.
 *
 * The list is fixed at deploy time (`create_app(mcp_hubs=[...])`), so it can
 * never go stale while the server is up — hence `staleTime: Infinity`, which
 * turns every visit to the MCP page after the first into a cache read. An
 * empty list is the normal state for a deployment that configured no hubs —
 * not an error.
 */
export function useMCPHubs() {
	const { data, isPending, error, refetch } = useQuery({
		queryKey: ['mcp-hubs'],
		queryFn: () => hubApi.mcp.listHubs(),
		staleTime: Infinity,
	});

	return {
		hubs: data ?? [],
		loading: isPending,
		error: error as Error | null,
		refetch: () => void refetch(),
	};
}
