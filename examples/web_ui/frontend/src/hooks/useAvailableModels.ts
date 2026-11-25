import { useQuery } from '@tanstack/react-query';

import { credentialApi, modelApi } from '@/api';
import type { CredentialView, ModelCard } from '@/api';
import { getUserId } from '@/api/client';

export interface CredentialWithModels {
	credential: CredentialView;
	models: ModelCard[];
}

function distinctOwnedOpenAICredentials(credentials: CredentialView[]) {
	const owner = getUserId();
	const seen = new Set<string>();

	return credentials.filter(({ user_id, data }) => {
		if (
			user_id !== owner ||
			data.type !== 'openai_credential' ||
			typeof data.api_key !== 'string'
		) {
			return true;
		}
		const settings = Object.keys(data)
			.filter((key) => key !== 'id')
			.sort()
			.map((key) => [key, data[key]]);
		const signature = JSON.stringify(settings);
		if (seen.has(signature)) return false;
		seen.add(signature);
		return true;
	});
}

/**
 * Fetches all credentials and their available models, grouped by provider type.
 * Provider type is read from `credential.data.type`.
 * Each credential can expose a different set of models.
 *
 * One credential list plus one model list per credential — the most expensive
 * fan-out on the page, and every model picker in the app mounts it. Cached
 * under a shared query key that credential mutations invalidate.
 */
async function fetchGroups(): Promise<Record<string, CredentialWithModels[]>> {
	const { credentials } = await credentialApi.list();
	const result: Record<string, CredentialWithModels[]> = {};

	await Promise.all(
		distinctOwnedOpenAICredentials(credentials).map(async (credential) => {
			const type = credential.data.type as string | undefined;
			if (!type) return;
			if (!result[type]) result[type] = [];
			try {
				const { models } = await modelApi.list(type, credential.id);
				// Newest releases first; unknown dates last, then model name.
				result[type].push({
					credential,
					models: [...models].sort((a, b) => {
						const byReleaseDate = (b.release_date ?? '').localeCompare(
							a.release_date ?? '',
						);
						return (
							byReleaseDate ||
							b.name.localeCompare(a.name, undefined, { numeric: true })
						);
					}),
				});
			} catch {
				result[type].push({ credential, models: [] });
			}
		}),
	);

	return result;
}

/**
 * Cache key for the grouped model list. Exported so a credential change —
 * which moves what these groups contain — can invalidate it.
 */
export const AVAILABLE_MODELS_KEY = ['available-models'];

export function useAvailableModels() {
	const { data, isPending, error } = useQuery({
		queryKey: AVAILABLE_MODELS_KEY,
		queryFn: fetchGroups,
	});

	return {
		groups: data ?? {},
		loading: isPending,
		error: error as Error | null,
	};
}
