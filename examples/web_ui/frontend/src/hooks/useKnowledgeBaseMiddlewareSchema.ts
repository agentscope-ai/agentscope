import { useEffect, useState } from 'react';

import { knowledgeBaseApi } from '@/api';
import type { JSONSchema } from '@/api';

/**
 * Module-level cache for the KB middleware parameter schema.
 *
 * The schema is class-static on the backend (derived from
 * :class:`KnowledgeBaseMiddleware.Parameters.model_json_schema()`),
 * so a single fetch per page load is enough.  Several mounted
 * `KnowledgeBasePanel`s would otherwise each fire the request — the
 * cache de-duplicates concurrent callers, too.
 */
let cached: JSONSchema | null = null;
let inflight: Promise<JSONSchema> | null = null;

async function fetchSchema(): Promise<JSONSchema> {
	if (cached) return cached;
	if (inflight) return inflight;
	inflight = knowledgeBaseApi
		.middlewareParametersSchema()
		.then((res) => {
			cached = res.parameter_schema as unknown as JSONSchema;
			return cached;
		})
		.finally(() => {
			inflight = null;
		});
	return inflight;
}

/**
 * Fetch the parameter schema for the session-level
 * `KnowledgeBaseMiddleware`.
 *
 * Returns the schema once available; `null` while loading. The schema
 * is shared across all callers via a module-level cache.
 */
export function useKnowledgeBaseMiddlewareSchema(): {
	schema: JSONSchema | null;
	loading: boolean;
	error: Error | null;
} {
	const [schema, setSchema] = useState<JSONSchema | null>(cached);
	const [loading, setLoading] = useState(cached === null);
	const [error, setError] = useState<Error | null>(null);

	useEffect(() => {
		if (cached) {
			setSchema(cached);
			return;
		}
		let cancelled = false;
		setLoading(true);
		fetchSchema()
			.then((s) => {
				if (!cancelled) setSchema(s);
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

	return { schema, loading, error };
}
