import { client } from './client';
import type {
	AgentEvent,
	CreateSessionRequest,
	CreateSessionResponse,
	InterruptSessionResponse,
	SessionListResponse,
	SessionRecord,
	UpdateSessionRequest,
	Msg,
} from './types';

export interface MessagesResponse {
	messages: Msg[];
	is_running: boolean;
}

export const sessionApi = {
	list: (agentId: string) => client.get<SessionListResponse>('/sessions/', { agent_id: agentId }),

	create: (body: CreateSessionRequest) => client.post<CreateSessionResponse>('/sessions/', body),

	update: (sessionId: string, agentId: string, body: UpdateSessionRequest) =>
		client.patch<SessionRecord>(`/sessions/${sessionId}`, body, { agent_id: agentId }),

	delete: (sessionId: string, agentId: string) =>
		client.delete(`/sessions/${sessionId}`, { agent_id: agentId }),

	/**
	 * Request interruption of an in-progress reply (running or parked).
	 *
	 * Backend contract:
	 * - 202 Accepted → returns `InterruptSessionResponse`; the cancel
	 *   signal was broadcast (running) or a wakeup-interrupt was
	 *   enqueued (parked). Idempotent: an idle target is a silent
	 *   no-op at the agent layer.
	 * - 404 Not Found → the session does not exist.
	 */
	interrupt: (sessionId: string, agentId: string) =>
		client.post<InterruptSessionResponse>(`/sessions/${sessionId}/interrupt`, null, {
			agent_id: agentId,
		}),

	messages: (sessionId: string, agentId: string, offset = 0, limit = 50) =>
		client.get<MessagesResponse>(`/sessions/${sessionId}/messages`, {
			agent_id: agentId,
			offset: String(offset),
			limit: String(limit),
		}),

	/**
	 * Subscribe to a session's live event stream via SSE.
	 *
	 * Opens a long-lived ``GET /sessions/{sid}/stream`` connection and
	 * yields each ``AgentEvent`` as it arrives. The connection stays
	 * open until the caller aborts via the ``signal`` or closes the
	 * generator.
	 *
	 * Uses fetch-based SSE (not native ``EventSource``) so the
	 * ``X-User-ID`` custom header is sent.
	 *
	 * @param sessionId - The session to subscribe to.
	 * @param agentId - The agent that owns the session.
	 * @param signal - Optional abort signal to close the connection.
	 * @returns An async generator yielding ``AgentEvent`` objects.
	 */
	streamEvents: async function* (
		sessionId: string,
		agentId: string,
		signal?: AbortSignal,
	): AsyncGenerator<AgentEvent> {
		const res = await client.stream(`/sessions/${sessionId}/stream`, {
			method: 'GET',
			params: { agent_id: agentId },
			signal,
		});

		const reader = res.body!.getReader();
		const decoder = new TextDecoder();
		let buffer = '';

		try {
			while (true) {
				const { done, value } = await reader.read();
				if (done) break;

				buffer += decoder.decode(value, { stream: true });
				const lines = buffer.split('\n');
				buffer = lines.pop() ?? '';

				for (const line of lines) {
					if (line.startsWith('data: ')) {
						const json = line.slice(6).trim();
						if (json) yield JSON.parse(json) as AgentEvent;
					}
					// SSE comment frames (`:...\n`) are silently skipped
					// (used for heartbeats).
				}
			}
		} finally {
			reader.releaseLock();
		}
	},
};
