import { useCallback, useEffect, useState } from 'react';

import { knowledgeBaseApi } from '@/api';
import type { ChunkerInfo } from '@/api';

/**
 * Fetches the list of registered chunker types and their parameter
 * schemas. Used by the create-KB dialog to render the chunker
 * selector and dynamic parameter form.
 */
export function useChunkers() {
	const [chunkers, setChunkers] = useState<ChunkerInfo[]>([]);
	const [loading, setLoading] = useState(false);

	const refetch = useCallback(async () => {
		setLoading(true);
		try {
			const res = await knowledgeBaseApi.listChunkers();
			setChunkers(res.chunkers);
		} catch {
			// Graceful fallback — if the endpoint is unavailable
			// (older backend), the dialog works without chunker
			// selection.
		} finally {
			setLoading(false);
		}
	}, []);

	useEffect(() => {
		refetch();
	}, [refetch]);

	return { chunkers, loading, refetch };
}
