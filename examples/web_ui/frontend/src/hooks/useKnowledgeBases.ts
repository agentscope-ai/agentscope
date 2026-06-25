import { useCallback, useEffect, useState } from 'react';

import { knowledgeBaseApi } from '@/api';
import type {
	CreateKnowledgeBaseRequest,
	KnowledgeBaseView,
	SearchKnowledgeBaseRequest,
	UpdateKnowledgeBaseRequest,
} from '@/api';

/**
 * Knowledge base CRUD + search wrapper.
 *
 * Loads the caller's knowledge bases from `/knowledge_bases/` on mount
 * and refetches after every mutation, so the UI stays consistent with
 * the server-side state.
 */
export function useKnowledgeBases() {
	const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBaseView[]>([]);
	const [loading, setLoading] = useState(false);
	const [creating, setCreating] = useState(false);
	const [error, setError] = useState<Error | null>(null);

	const refetch = useCallback(async () => {
		setLoading(true);
		setError(null);
		try {
			const { knowledge_bases } = await knowledgeBaseApi.list();
			setKnowledgeBases(knowledge_bases);
		} catch (e) {
			setError(e as Error);
		} finally {
			setLoading(false);
		}
	}, []);

	useEffect(() => {
		refetch();
	}, [refetch]);

	/** Create a new knowledge base and refresh the list. */
	const create = useCallback(
		async (body: CreateKnowledgeBaseRequest): Promise<string> => {
			setCreating(true);
			setError(null);
			try {
				const { knowledge_base_id } = await knowledgeBaseApi.create(body);
				await refetch();
				return knowledge_base_id;
			} catch (e) {
				setError(e as Error);
				throw e;
			} finally {
				setCreating(false);
			}
		},
		[refetch],
	);

	/** Permanently delete a knowledge base and refresh the list. */
	const remove = useCallback(
		async (knowledgeBaseId: string) => {
			await knowledgeBaseApi.delete(knowledgeBaseId);
			await refetch();
		},
		[refetch],
	);

	/** Update mutable fields on a knowledge base and refresh the list. */
	const update = useCallback(
		async (
			knowledgeBaseId: string,
			body: UpdateKnowledgeBaseRequest,
		): Promise<KnowledgeBaseView> => {
			const view = await knowledgeBaseApi.update(knowledgeBaseId, body);
			await refetch();
			return view;
		},
		[refetch],
	);

	/** Upload a document into a knowledge base. */
	const uploadDocument = useCallback(
		(knowledgeBaseId: string, file: File) =>
			knowledgeBaseApi.uploadDocument(knowledgeBaseId, file),
		[],
	);

	/** Delete a document from a knowledge base. */
	const deleteDocument = useCallback(
		(knowledgeBaseId: string, documentId: string) =>
			knowledgeBaseApi.deleteDocument(knowledgeBaseId, documentId),
		[],
	);

	/** Search a knowledge base by natural-language query. */
	const search = useCallback(
		(knowledgeBaseId: string, body: SearchKnowledgeBaseRequest) =>
			knowledgeBaseApi.search(knowledgeBaseId, body),
		[],
	);

	return {
		knowledgeBases,
		loading,
		creating,
		error,
		refetch,
		create,
		remove,
		update,
		uploadDocument,
		deleteDocument,
		search,
	};
}
