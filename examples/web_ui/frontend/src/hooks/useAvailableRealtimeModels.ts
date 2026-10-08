import { useQuery } from '@tanstack/react-query';

import { credentialApi, realtimeModelApi } from '@/api';
import type { CredentialView, RealtimeModelCard } from '@/api';

interface CredentialWithRealtimeModels {
	credential: CredentialView;
	models: RealtimeModelCard[];
}

async function fetchGroups(): Promise<Record<string, CredentialWithRealtimeModels[]>> {
	const { credentials } = await credentialApi.list();
	const result: Record<string, CredentialWithRealtimeModels[]> = {};
	const credentialsByProvider: Record<string, CredentialView[]> = {};

	for (const credential of credentials) {
		const provider = credential.data.type as string | undefined;
		if (!provider) continue;
		(credentialsByProvider[provider] ??= []).push(credential);
	}

	await Promise.all(
		Object.entries(credentialsByProvider).map(async ([provider, providerCredentials]) => {
			try {
				const { models } = await realtimeModelApi.list(provider);
				if (models.length === 0) return;
				const sortedModels = [...models].sort((a, b) => {
					const byReleaseDate = (b.release_date ?? '').localeCompare(
						a.release_date ?? '',
					);
					return (
						byReleaseDate || b.name.localeCompare(a.name, undefined, { numeric: true })
					);
				});
				result[provider] = providerCredentials.map((credential) => ({
					credential,
					models: sortedModels,
				}));
			} catch {
				// A credential without realtime support is not an error for this picker.
			}
		}),
	);

	return result;
}

export const AVAILABLE_REALTIME_MODELS_KEY = ['available-realtime-models'];

export function useAvailableRealtimeModels() {
	const { data, isPending } = useQuery({
		queryKey: AVAILABLE_REALTIME_MODELS_KEY,
		queryFn: fetchGroups,
	});
	return {
		groups: data ?? {},
		loading: isPending,
	};
}
