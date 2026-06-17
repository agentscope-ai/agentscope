import { useState, useEffect, useCallback } from 'react';

import { credentialApi, ttsModelApi } from '@/api';
import type { CredentialRecord, TTSModelCard } from '@/api';

export interface CredentialWithTTSModels {
	credential: CredentialRecord;
	models: TTSModelCard[];
}

/**
 * Fetches all credentials and their available TTS models, grouped by provider type.
 * Credentials/providers that expose no TTS models are omitted.
 */
export function useAvailableTTSModels() {
	const [groups, setGroups] = useState<Record<string, CredentialWithTTSModels[]>>({});
	const [loading, setLoading] = useState(false);
	const [error, setError] = useState<Error | null>(null);

	const refetch = useCallback(async () => {
		setLoading(true);
		setError(null);
		try {
			const { credentials } = await credentialApi.list();
			const result: Record<string, CredentialWithTTSModels[]> = {};

			await Promise.all(
				credentials.map(async (credential) => {
					const type = credential.data.type as string | undefined;
					if (!type) return;
					if (!result[type]) result[type] = [];
					try {
						const { models } = await ttsModelApi.list(type);
						if (models.length > 0) {
							result[type].push({ credential, models });
						}
					} catch {
						// Provider doesn't support TTS — skip silently
					}
				}),
			);

			// Remove provider groups with no TTS models
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
