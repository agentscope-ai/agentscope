import { useState, useCallback } from 'react';

import { chatApi } from '@/api';
import type { ChatRequest } from '@/api';

/**
 * Fire-and-forget chat trigger.
 *
 * Sends a ``POST /chat/`` request that kicks off a chat run on the
 * backend. Events are **not** returned here — they arrive via the
 * session's SSE stream (``GET /sessions/{sid}/stream``), consumed by
 * :func:`useMessages`.
 *
 * This hook is a thin wrapper around ``chatApi.trigger``; it mainly
 * exists for parity with the previous ``useChat`` API shape.
 */
export function useChat() {
	const [streaming, setStreaming] = useState(false);
	const [error, setError] = useState<Error | null>(null);

	/**
	 * Trigger a chat run. Returns when the POST completes (not when
	 * the run finishes).
	 *
	 * @param body - The chat request payload.
	 */
	const send = useCallback(async (body: ChatRequest) => {
		setStreaming(true);
		setError(null);

		try {
			await chatApi.trigger(body);
		} catch (e) {
			if ((e as Error).name !== 'AbortError') setError(e as Error);
		} finally {
			setStreaming(false);
		}
	}, []);

	return { streaming, error, send };
}
