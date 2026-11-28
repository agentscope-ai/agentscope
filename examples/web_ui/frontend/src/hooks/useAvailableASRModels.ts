import { useQuery } from '@tanstack/react-query';

import { asrModelApi, credentialApi } from '@/api';
import type { ASRModelCard, ASRModelConfig, CredentialView } from '@/api';

export interface CredentialWithASRModels {
	credential: CredentialView;
	models: ASRModelCard[];
}

export function findASRModelCard(
	groups: Record<string, CredentialWithASRModels[]>,
	config: ASRModelConfig | null,
): ASRModelCard | null {
	if (!config) return null;
	const item = groups[config.type]?.find(
		({ credential }) => credential.id === config.credential_id,
	);
	return item?.models.find((model) => model.name === config.model) ?? null;
}

async function fetchGroups(): Promise<Record<string, CredentialWithASRModels[]>> {
	const { credentials } = await credentialApi.list();
	const result: Record<string, CredentialWithASRModels[]> = {};
	const credentialsByProvider: Record<string, CredentialView[]> = {};

	for (const credential of credentials) {
		const provider = credential.data.type as string | undefined;
		if (!provider) continue;
		(credentialsByProvider[provider] ??= []).push(credential);
	}

	await Promise.all(
		Object.entries(credentialsByProvider).map(async ([provider, providerCredentials]) => {
			try {
				const response = await asrModelApi.list(provider);
				const models = response.models;
				if (models.length === 0) return;
				result[provider] = providerCredentials.map((credential) => ({
					credential,
					models,
				}));
			} catch {
				// A provider without ASR support is not an error here.
			}
		}),
	);

	return result;
}

export const AVAILABLE_ASR_MODELS_KEY = ['available-asr-models'];

/** Fetch batch and realtime ASR models for every configured credential. */
export function useAvailableASRModels() {
	const { data, isPending, error } = useQuery({
		queryKey: AVAILABLE_ASR_MODELS_KEY,
		queryFn: fetchGroups,
	});
	return {
		groups: data ?? {},
		loading: isPending,
		error: error as Error | null,
	};
}
