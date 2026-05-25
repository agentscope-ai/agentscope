import { useState, useEffect, useCallback } from 'react';

import { credentialApi } from '../api';
import type { CredentialRecord, CreateCredentialRequest, UpdateCredentialRequest } from '../api';

/**
 * Manages API key credentials with CRUD operations.
 * Fetches on mount and automatically re-fetches after each mutation.
 */
export function useCredentials() {
	const [credentials, setCredentials] = useState<CredentialRecord[]>([]);
	const [loading, setLoading] = useState(false);
	const [error, setError] = useState<Error | null>(null);

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

	/** Stores a new credential and refreshes the list. */
	const create = useCallback(
		async (body: CreateCredentialRequest) => {
			const res = await credentialApi.create(body);
			await refetch();
			return res;
		},
		[refetch],
	);

	/** Replaces a credential's payload and refreshes the list. */
	const update = useCallback(
		async (credentialId: string, body: UpdateCredentialRequest) => {
			const res = await credentialApi.update(credentialId, body);
			await refetch();
			return res;
		},
		[refetch],
	);

	/** Permanently deletes a credential and refreshes the list. */
	const remove = useCallback(
		async (credentialId: string) => {
			await credentialApi.delete(credentialId);
			await refetch();
		},
		[refetch],
	);

	return { credentials, loading, error, refetch, create, update, remove };
}
