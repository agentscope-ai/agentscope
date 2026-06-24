import { useState, useEffect, useCallback } from 'react';

import { credentialApi, realtimeModelApi } from '@/api';
import type { CredentialRecord, RealtimeModelCard } from '@/api';

export interface CredentialWithRealtimeModels {
	credential: CredentialRecord;
	models: RealtimeModelCard[];
}

/**
 * Fetches all credentials and their available realtime models, grouped by provider type.
 * Credentials/providers that expose no realtime models are omitted.
 */
export function useAvailableRealtimeModels() {
	const [groups, setGroups] = useState<Record<string, CredentialWithRealtimeModels[]>>({});
	const [loading, setLoading] = useState(false);
	const [error, setError] = useState<Error | null>(null);

	const refetch = useCallback(async () => {
		setLoading(true);
		setError(null);
		try {
			const { credentials } = await credentialApi.list();
			const result: Record<string, CredentialWithRealtimeModels[]> = {};

			await Promise.all(
				credentials.map(async (credential) => {
					const type = credential.data.type as string | undefined;
					if (!type) return;
					if (!result[type]) result[type] = [];
					try {
						const { models } = await realtimeModelApi.list(type);
						if (models.length > 0) {
							result[type].push({ credential, models });
						}
					} catch {
						// Provider doesn't support realtime — skip silently
					}
				}),
			);

			for (const key of Object.keys(result)) {
				if (result[key].length === 0) delete result[key];
			}

			setGroups(result);
		} catch (e) {
			setError(e as Error);
		} finally {
			setLoading(false);
		}
	}, []);

	useEffect(() => {
		refetch();
	}, [refetch]);

	return { groups, loading, error, refetch };
}
