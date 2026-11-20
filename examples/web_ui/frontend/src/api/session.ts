import { client, type ApiRequestOptions } from './client';
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
	event_cursor: string | null;
	is_running: boolean;
	has_more: boolean;
}

export interface InputResult {
	queue_item_id?: string;
	state: 'unknown' | 'reply' | 'finished';
	reply_id: string | null;
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
	inputResult: (
		sessionId: string,
		agentId: string,
		messageId: string,
		submissionId: string,
		options?: ApiRequestOptions,
	) =>
		client.get<InputResult>(
			`/sessions/${sessionId}/inputs/${messageId}/result`,
			{ agent_id: agentId, submission_id: submissionId },
			options,
		),
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
	 * When replyId is provided, later replies are never interrupted by this request.
	 *
	 * Backend contract:
	 * - 202 Accepted → returns `InterruptSessionResponse`; the cancel
	 *   signal was broadcast (running) or a wakeup-interrupt was
	 *   enqueued (parked). Idempotent: an idle target is a silent
	 *   no-op at the agent layer.
	 * - 404 Not Found → the session does not exist.
	 */
	interrupt: (
		sessionId: string,
		agentId: string,
		replyId?: string,
		options?: ApiRequestOptions,
	) =>
		client.post<InterruptSessionResponse>(
			`/sessions/${sessionId}/interrupt`,
			null,
			{ agent_id: agentId, ...(replyId !== undefined && { reply_id: replyId }) },
			options,
		),

	messages: (
		sessionId: string,
		agentId: string,
		params?: { before?: string; limit?: number },
		options?: ApiRequestOptions,
	) =>
		client.get<MessagesResponse>(
			`/sessions/${sessionId}/messages`,
			{
				agent_id: agentId,
				...(params?.before != null && { before: params.before }),
				...(params?.limit != null && { limit: String(params.limit) }),
			},
			options,
		),

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
	 * @param after - Optional persisted-event cursor to resume after.
	 * @returns An async generator yielding ``AgentEvent`` objects.
	 */
	streamEvents: async function* (
		sessionId: string,
		agentId: string,
		signal?: AbortSignal,
		after?: string | null,
		onConnected?: () => void,
		options?: Pick<ApiRequestOptions, 'baseUrl' | 'userId' | 'silent'> & {
			onReplayComplete?: () => void;
		},
	): AsyncGenerator<AgentEvent> {
		const { onReplayComplete, ...requestOptions } = options ?? {};
		const res = await client.stream(`/sessions/${sessionId}/stream`, {
			method: 'GET',
			params: {
				agent_id: agentId,
				...(after != null && { after }),
				...(onReplayComplete && { replay_notice: 'true' }),
			},
			signal,
			...requestOptions,
		});

		const reader = res.body!.getReader();
		onConnected?.();
		const decoder = new TextDecoder();
		const fragments: string[] = [];
		let finishReplay = onReplayComplete;

		try {
			while (true) {
				const { done, value } = await reader.read();
				if (done) break;

				const lines = decoder.decode(value, { stream: true }).split('\n');
				const tail = lines.pop()!;
				for (let line of lines) {
					if (fragments.length) {
						fragments.push(line);
						line = fragments.join('');
						fragments.length = 0;
					}
					if (line.startsWith('data: ')) {
						const json = line.slice(6).trim();
						if (json) yield JSON.parse(json) as AgentEvent;
					} else if (finishReplay && line.startsWith(':')) {
						// Older servers send only unlabelled heartbeats.
						const comment = line.trimEnd();
						if (comment === ': replay-complete' || comment === ':') {
							finishReplay();
							finishReplay = undefined;
						}
					}
				}
				if (tail) fragments.push(tail);
			}
		} finally {
			reader.releaseLock();
		}
	},
};
