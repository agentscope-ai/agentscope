import { useCallback, useEffect, useRef, useState } from 'react';

import { knowledgeBaseApi } from '@/api';
import type { KnowledgeDocumentView } from '@/api';

/**
 * Owns the document list for a single knowledge base.
 *
 * Re-fetches on mount, when `knowledgeBaseId` changes, and via the
 * caller-driven `refetch`. In-flight uploads render from the upload
 * provider until their record shows up here, so the only refetch the
 * panel owes is the one after a polling tick lifts a row to a terminal
 * state — that is what makes `chunk_count` reflect the worker's final
 * commit.
 */
export function useKnowledgeDocuments(knowledgeBaseId: string | null) {
	const [documents, setDocuments] = useState<KnowledgeDocumentView[]>([]);
	// Starts true when there is something to fetch, so the first render
	// already says "loading" instead of "no documents".
	const [loading, setLoading] = useState(knowledgeBaseId !== null);
	const [error, setError] = useState<Error | null>(null);
	// Discards stale responses if the user switches KBs mid-flight.
	const requestSeq = useRef(0);

	const refetch = useCallback(async () => {
		if (!knowledgeBaseId) {
			setDocuments([]);
			setLoading(false);
			return;
		}
		const seq = ++requestSeq.current;
		setLoading(true);
		setError(null);
		try {
			// The panel merges rows with in-flight upload tasks and the
			// status poller into one flat list, so drain all pages of
			// the paginated endpoint.
			const list = await knowledgeBaseApi.listAllDocuments(knowledgeBaseId);
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
