import { useEffect, useState } from 'react';

import { knowledgeBaseApi } from '@/api';
import type { ListSupportedContentTypesResponse } from '@/api';

/**
 * Module-level cache for the KB-parser capability list.
 *
 * The set of supported media types / extensions is fixed at app
 * startup — it is derived from the parser registry handed to
 * `create_app()`, which never changes for the lifetime of the process.
 * One fetch per page load is enough, and several mounted document
 * panels share the same cached value.
 */
let cached: ListSupportedContentTypesResponse | null = null;
let inflight: Promise<ListSupportedContentTypesResponse> | null = null;

async function fetchSupported(): Promise<ListSupportedContentTypesResponse> {
	if (cached) return cached;
	if (inflight) return inflight;
	inflight = knowledgeBaseApi
		.supportedContentTypes()
		.then((res) => {
			cached = res;
			return res;
		})
		.finally(() => {
			inflight = null;
		});
	return inflight;
}

/**
 * Fetch the parser-supported upload types.
 *
 * Returns the union of media types and extensions once available; an
 * empty placeholder while loading.  The result is shared across all
 * callers via a module-level cache.
 */
export function useKnowledgeSupportedContentTypes(): {
	mediaTypes: string[];
	extensions: string[];
	loading: boolean;
	error: Error | null;
} {
	const [data, setData] = useState<ListSupportedContentTypesResponse | null>(cached);
	const [loading, setLoading] = useState(cached === null);
	const [error, setError] = useState<Error | null>(null);

	useEffect(() => {
		if (cached) {
			setData(cached);
			return;
		}
		let cancelled = false;
		setLoading(true);
		fetchSupported()
			.then((res) => {
				if (!cancelled) setData(res);
			})
			.catch((e) => {
				if (!cancelled) setError(e as Error);
			})
			.finally(() => {
				if (!cancelled) setLoading(false);
			});
		return () => {
			cancelled = true;
		};
	}, []);

	return {
		mediaTypes: data?.media_types ?? [],
		extensions: data?.extensions ?? [],
		loading,
		error,
	};
}
