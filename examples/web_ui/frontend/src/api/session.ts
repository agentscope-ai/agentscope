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
	has_more: boolean;
}

/**
 * Sessions this tab created and has not opened yet.
 *
 * A session created here cannot have any history, so asking the server
 * for it is a guaranteed-empty round trip — and one that paints a
 * loading state over a conversation the user is about to start.
 *
 * Entries are consumed on first read: once the session has been opened,
 * anything written to it afterwards (a scheduled run, a team member)
 * must be fetched normally.
 */
const freshlyCreated = new Set<string>();

/**
 * Whether `sessionId` was created by this tab and not yet opened.
 * Consumes the flag, so a second call for the same id returns false.
 *
 * @param sessionId - The session about to be opened.
 * @returns True when its history can safely be assumed empty.
 */
export function takeFreshlyCreated(sessionId: string): boolean {
	return freshlyCreated.delete(sessionId);
}

export const sessionApi = {
	list: (agentId: string) => client.get<SessionListResponse>('/sessions/', { agent_id: agentId }),

	create: async (body: CreateSessionRequest) => {
		const res = await client.post<CreateSessionResponse>('/sessions/', body);
		freshlyCreated.add(res.session_id);
		return res;
	},

	/**
	 * Update a session's configuration.
	 *
	 * Returns 409 while a chat run holds the session — the agent
	 * snapshots its configuration at run start, so the change could not
	 * apply to the reply in flight. Pass `silent` for automatic writes
	 * the user did not initiate, where a toast would be noise.
	 */
	update: (
		sessionId: string,
		agentId: string,
		body: UpdateSessionRequest,
		options?: { silent?: boolean },
	) =>
		client.patch<SessionRecord>(`/sessions/${sessionId}`, body, { agent_id: agentId }, options),

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

	messages: (sessionId: string, agentId: string, params?: { before?: string; limit?: number }) =>
		client.get<MessagesResponse>(`/sessions/${sessionId}/messages`, {
			agent_id: agentId,
			...(params?.before != null && { before: params.before }),
			...(params?.limit != null && { limit: String(params.limit) }),
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
