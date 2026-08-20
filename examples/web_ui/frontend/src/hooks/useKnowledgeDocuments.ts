import { useCallback, useEffect, useRef, useState } from 'react';

import { knowledgeBaseApi } from '@/api';
import type { KnowledgeDocumentView } from '@/api';

/**
 * Owns the document list for a single knowledge base.
 *
 * Re-fetches on mount, when `knowledgeBaseId` changes, and via the
 * caller-driven `refetch`. The upload page should call `refetch` after
 * a successful upload (so the new `pending` row appears) and again
 * whenever a polling tick lifts a row to a terminal state (so
 * `chunk_count` reflects the worker's final commit).
 */
export function useKnowledgeDocuments(knowledgeBaseId: string | null) {
	const [documents, setDocuments] = useState<KnowledgeDocumentView[]>([]);
	const [loading, setLoading] = useState(false);
	const [error, setError] = useState<Error | null>(null);
	// Discards stale responses if the user switches KBs mid-flight.
	const requestSeq = useRef(0);

	const refetch = useCallback(async () => {
		if (!knowledgeBaseId) {
			setDocuments([]);
			return;
		}
		const seq = ++requestSeq.current;
		setLoading(true);
		setError(null);
		try {
			// The panel merges rows with in-flight upload tasks and the
			// status poller, so it renders one flat list — request the
			// max page size instead of paginating for now.
			const { documents: list } = await knowledgeBaseApi.listDocuments(knowledgeBaseId, {
				page_size: 128,
			});
			if (seq !== requestSeq.current) return;
			setDocuments(list);
		} catch (e) {
			if (seq !== requestSeq.current) return;
			setError(e as Error);
		} finally {
			if (seq === requestSeq.current) setLoading(false);
		}
	}, [knowledgeBaseId]);

	useEffect(() => {
		void refetch();
	}, [refetch]);

	return { documents, loading, error, refetch, setDocuments };
}
