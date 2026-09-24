import { useQuery } from '@tanstack/react-query';

import { credentialApi, modelApi } from '@/api';
import type { CredentialView, ModelCard } from '@/api';

export interface CredentialWithModels {
	credential: CredentialView;
	models: ModelCard[];
}

/**
 * Fetches all credentials and their available models, grouped by provider type.
 * Provider type is read from `credential.data.type`.
 * Credentials without a `type` field or whose model fetch fails are silently skipped.
 *
 * One credential list plus one model list per provider — the most expensive
 * fan-out on the page, and every model picker in the app mounts it. Cached
 * under the shared default window and re-fetched on demand through
 * `refetch`, which is what the "credential just added" trigger calls.
 */
async function fetchGroups(): Promise<Record<string, CredentialWithModels[]>> {
	const { credentials } = await credentialApi.list();
	const result: Record<string, CredentialWithModels[]> = {};

	await Promise.all(
		credentials.map(async (credential) => {
			const type = credential.data.type as string | undefined;
			if (!type) return;
			if (!result[type]) result[type] = [];
			try {
				const { models } = await modelApi.list(type);
				// Reverse-alphabetical, which is how the providers' naming
				// schemes rank themselves — gpt-5 before gpt-4, qwen3 before
				// qwen2 — so the strongest models sit at the top of the picker.
				result[type].push({
					credential,
					models: [...models].sort((a, b) =>
						b.name.localeCompare(a.name, undefined, { numeric: true }),
					),
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
	const { data, isPending, error, refetch } = useQuery({
		queryKey: AVAILABLE_MODELS_KEY,
		queryFn: fetchGroups,
	});

	return {
		groups: data ?? {},
		loading: isPending,
		error: error as Error | null,
		refetch: () => void refetch(),
	};
}
