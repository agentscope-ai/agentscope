import { useState, useCallback } from 'react';

import { modelApi } from '../api';
import type { ModelCard } from '../api';

/**
 * Fetches candidate models for a given provider on demand.
 * Does not auto-fetch — call `fetch(provider)` explicitly.
 */
export function useModels() {
	const [models, setModels] = useState<ModelCard[]>([]);
	const [loading, setLoading] = useState(false);
	const [error, setError] = useState<Error | null>(null);

	/**
	 * Loads the model list for the specified provider type.
	 * @param provider - e.g. "openai", "dashscope"
	 */
	const fetch = useCallback(async (provider: string) => {
		setLoading(true);
		setError(null);
		try {
			const res = await modelApi.list(provider);
			setModels(res.models);
		} catch (e) {
			setError(e as Error);
		} finally {
			setLoading(false);
		}
	}, []);

	return { models, loading, error, fetch };
}
