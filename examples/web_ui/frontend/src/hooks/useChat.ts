import { useState, useCallback, useRef } from 'react';

import { chatApi } from '@/api';
import type { AgentEvent, ChatRequest } from '@/api';

/**
 * Drives a streaming chat session with an agent.
 *
 * Collects SSE AgentEvent frames into `events` as they arrive.
 * Call `abort()` to cancel an in-flight stream.
 */
export function useChat() {
	const [events, setEvents] = useState<AgentEvent[]>([]);
	const [streaming, setStreaming] = useState(false);
	const [error, setError] = useState<Error | null>(null);
	const abortRef = useRef<AbortController | null>(null);

	/**
	 * Sends a message and streams the agent's reply into `events`.
	 * Cancels any previous in-flight stream before starting.
	 */
	const send = useCallback(async (body: ChatRequest) => {
		abortRef.current?.abort();
		const controller = new AbortController();
		abortRef.current = controller;

		setEvents([]);
		setStreaming(true);
		setError(null);

		try {
			for await (const event of chatApi.stream(body, controller.signal)) {
				setEvents((prev) => [...prev, event]);
			}
		} catch (e) {
			if ((e as Error).name !== 'AbortError') setError(e as Error);
		} finally {
			setStreaming(false);
		}
	}, []);

	/** Cancels the current in-flight stream. */
	const abort = useCallback(() => {
		abortRef.current?.abort();
	}, []);

	return { events, streaming, error, send, abort };
}
