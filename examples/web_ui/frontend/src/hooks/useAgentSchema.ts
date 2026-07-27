import { useEffect, useState } from 'react';

import { agentApi } from '@/api';
import type { AgentSchemaV2Response } from '@/api';

/**
 * Module-level cache. The agent schema is derived from Pydantic models
 * on the backend and never changes at runtime, so a single in-flight fetch
 * is enough for the whole session.
 */
let cached: AgentSchemaV2Response | null = null;
let inflight: Promise<AgentSchemaV2Response> | null = null;

async function loadSchema(): Promise<AgentSchemaV2Response> {
	if (cached) return cached;
	if (!inflight) {
		inflight = agentApi.getSchema().then((res) => {
			cached = res;
			return res;
		});
	}
	return inflight;
}

/** Fetch (and cache) the full `AgentData` JSON Schema for the agent form. */
export function useAgentSchema() {
	const [schema, setSchema] = useState<AgentSchemaV2Response | null>(cached);
	const [error, setError] = useState<Error | null>(null);

	useEffect(() => {
		if (cached) return;
		loadSchema()
			.then(setSchema)
			.catch((e: Error) => setError(e));
	}, []);

	return { schema, loading: schema === null && error === null, error };
}
