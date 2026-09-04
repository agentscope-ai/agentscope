import { useQueryClient } from '@tanstack/react-query';
import { useState, useEffect, useCallback } from 'react';

import { credentialApi } from '../api';
import type { CredentialView, CreateCredentialRequest, UpdateCredentialRequest } from '../api';
import { AVAILABLE_MODELS_KEY } from './useAvailableModels';

/**
 * Manages API key credentials with CRUD operations.
 * Fetches on mount and automatically re-fetches after each mutation.
 */
export function useCredentials() {
	const [credentials, setCredentials] = useState<CredentialView[]>([]);
	const [loading, setLoading] = useState(false);
	const [error, setError] = useState<Error | null>(null);
	const queryClient = useQueryClient();

	const refetch = useCallback(async () => {
		setLoading(true);
		setError(null);
		try {
			const res = await credentialApi.list();
			setCredentials(res.credentials);
		} catch (e) {
			setError(e as Error);
		} finally {
			setLoading(false);
		}
	}, []);

	useEffect(() => {
		refetch();
	}, [refetch]);

	// Every model picker reads its options from the cached
	// `available-models` query, which is derived from the credentials —
	// so a credential that just changed has to drop that cache too,
	// rather than leaving the pickers to time out of it.
	const refresh = useCallback(async () => {
		await refetch();
		await queryClient.invalidateQueries({ queryKey: AVAILABLE_MODELS_KEY });
	}, [refetch, queryClient]);

	/** Stores a new credential and refreshes the list. */
	const create = useCallback(
		async (body: CreateCredentialRequest) => {
			const res = await credentialApi.create(body);
			await refresh();
			return res;
		},
		[refresh],
	);

	/** Replaces a credential's payload and refreshes the list. */
	const update = useCallback(
		async (credentialId: string, body: UpdateCredentialRequest) => {
			const res = await credentialApi.update(credentialId, body);
			await refresh();
			return res;
		},
		[refresh],
	);

	/** Permanently deletes a credential and refreshes the list. */
	const remove = useCallback(
		async (credentialId: string) => {
			await credentialApi.delete(credentialId);
			await refresh();
		},
		[refresh],
	);

	return { credentials, loading, error, refetch, create, update, remove };
}
