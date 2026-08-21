import { useCallback, useEffect, useState } from 'react';

import { hubApi } from '@/api';
import type { HubInfo } from '@/api';

/**
 * The skill hubs registered on the server.
 *
 * The list is fixed at deploy time (`create_app(skill_hubs=[...])`), so it is
 * fetched once and never mutated from the UI. An empty list is the normal
 * state for a deployment that configured no hubs — not an error.
 */
export function useSkillHubs() {
	const [hubs, setHubs] = useState<HubInfo[]>([]);
	// Starts true: the first paint happens before the effect fires, and
	// a false start would flash the empty state before the spinner.
	const [loading, setLoading] = useState(true);
	const [error, setError] = useState<Error | null>(null);

	const refetch = useCallback(async () => {
		setLoading(true);
		setError(null);
		try {
			setHubs(await hubApi.skill.listHubs());
		} catch (e) {
			setError(e as Error);
		} finally {
			setLoading(false);
		}
	}, []);

	useEffect(() => {
		refetch();
	}, [refetch]);

	return { hubs, loading, error, refetch };
}
