import type { AgentEvent } from '@agentscope-ai/agentscope/event';
import type { ContentBlock } from '@agentscope-ai/agentscope/message';
import { useCallback, useEffect, useRef, useState } from 'react';

import { getBaseUrl, getUserId } from '@/api/client';

/**
 * Manages a WebSocket connection to the backend's realtime endpoint
 * (``WS /realtime/{sessionId}``).
 *
 * When ``enabled`` is ``true``, opens the WebSocket and routes incoming
 * ``AgentEvent`` frames through the provided ``processEvent`` callback
 * — the same callback used by ``useMessages`` for SSE events, so audio
 * output, text rendering, and message list updates all work without
 * changes.
 *
 * Exposes ``sendAudio(base64)`` for upstream audio from
 * ``useMicrophone`` and ``sendContent(blocks)`` for text/image input.
 *
 * @param agentId - The agent that owns the session.
 * @param sessionId - The session to connect.
 * @param enabled - Whether the WebSocket should be open.
 * @param processEvent - Event handler (shared with useMessages).
 * @returns Connection state + upstream senders.
 */
export function useRealtimeSession(
	agentId: string | null,
	sessionId: string | null,
	enabled: boolean,
	processEvent: ((event: AgentEvent, routeAudio?: boolean) => void) | null,
) {
	const [connected, setConnected] = useState(false);
	const [error, setError] = useState<Error | null>(null);
	const wsRef = useRef<WebSocket | null>(null);

	const processEventRef = useRef(processEvent);
	useEffect(() => {
		processEventRef.current = processEvent;
	}, [processEvent]);

	useEffect(() => {
		if (!enabled || !agentId || !sessionId) {
			setConnected(false);
			return;
		}

		let cancelled = false;
		let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
		let retryCount = 0;
		const MAX_RETRIES = 5;

		const baseUrl = getBaseUrl();
		const userId = getUserId();
		const wsBase = baseUrl.replace(/^http/, 'ws');
		const url = `${wsBase}/realtime/${sessionId}?agent_id=${encodeURIComponent(agentId)}&user_id=${encodeURIComponent(userId)}`;

		function connect() {
			if (cancelled) return;

			const ws = new WebSocket(url);
			wsRef.current = ws;

			ws.onopen = () => {
				if (cancelled) {
					ws.close();
					return;
				}
				retryCount = 0;
				setConnected(true);
				setError(null);
			};

			ws.onmessage = (event: MessageEvent) => {
				try {
					const data = JSON.parse(event.data as string) as AgentEvent;
					processEventRef.current?.(data, true);
				} catch {
					// skip malformed frames
				}
			};

			ws.onerror = () => {
				if (!cancelled) setError(new Error('WebSocket connection error'));
			};

			ws.onclose = () => {
				if (cancelled) return;
				setConnected(false);
				wsRef.current = null;
				// Exponential backoff reconnect
				if (retryCount < MAX_RETRIES) {
					const delay = Math.min(1000 * 2 ** retryCount, 16000);
					retryCount++;
					reconnectTimer = setTimeout(connect, delay);
				}
			};
		}

		connect();

		return () => {
			cancelled = true;
			if (reconnectTimer) clearTimeout(reconnectTimer);
			if (wsRef.current) {
				wsRef.current.close();
				wsRef.current = null;
			}
			setConnected(false);
		};
	}, [enabled, agentId, sessionId]);

	const sendAudio = useCallback((base64Pcm: string) => {
		const ws = wsRef.current;
		if (ws && ws.readyState === WebSocket.OPEN) {
			ws.send(JSON.stringify({ type: 'audio', data: base64Pcm }));
		}
	}, []);

	/**
	 * Send a tool-call user-confirmation via the WebSocket.
	 *
	 * The backend ``_upstream`` handler routes ``user_confirm`` frames
	 * to ``RealtimeAgent.handle_user_confirm()``, which resolves the
	 * pending permission future so tool execution can proceed.
	 */
	const sendConfirm = useCallback((data: Record<string, unknown>) => {
		const ws = wsRef.current;
		if (ws && ws.readyState === WebSocket.OPEN) {
			ws.send(JSON.stringify({ type: 'user_confirm', data }));
		}
	}, []);

	/** Send text/data content blocks through the WebSocket for the
	 *  realtime agent to process. */
	const sendContent = useCallback((blocks: ContentBlock[]) => {
		const ws = wsRef.current;
		if (ws && ws.readyState === WebSocket.OPEN) {
			ws.send(JSON.stringify({ type: 'content', blocks }));
		}
	}, []);

	return { connected, sendAudio, sendConfirm, sendContent, error };
}
