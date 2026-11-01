import type { Msg } from '@agentscope-ai/agentscope/message';

import { client } from './client';
import type { ChatRequest } from './types';

export interface ChatQueueItem {
	/** Stable business id used by queue mutation endpoints. */
	id: string;
	/** UTC ISO-8601 timestamp recorded when the item was queued. */
	created_at: string;
	/** One message or an ordered message group executed as one turn. */
	input: Msg | Msg[];
}

export interface ChatQueueResponse {
	/** Complete editable pending queue in FIFO order. */
	items: ChatQueueItem[];
}

/**
 * Chat API — accept queued user turns and fire-and-forget control triggers.
 *
 * Events produced by the run are delivered via the session's SSE
 * stream endpoint (``GET /sessions/{sid}/stream``), not in the
 * response body of this POST.
 */
export const chatApi = {
	/**
	 * Trigger a chat run for the specified session.
	 *
	 * Accepts user messages, human-in-the-loop confirmation events,
	 * or ``null`` (continue from current state). Returns immediately;
	 * the caller should already be subscribed to the session's SSE
	 * stream to receive the resulting events.
	 *
	 * @param body - The chat request payload.
	 * @returns Acceptance status (``queued`` for ordinary user turns).
	 */
	trigger: (body: ChatRequest) =>
		client.post<{
			status: 'started' | 'queued';
			session_id: string;
			queue_item_id: string | null;
		}>('/chat/', body),

	/**
	 * Read the complete editable pending queue.
	 *
	 * @param agentId - Agent that owns the target session.
	 * @param sessionId - Session whose FIFO should be read.
	 * @param silent - Suppress global error notifications for repair polling.
	 * @returns Current pending items in FIFO order.
	 */
	queue: (agentId: string, sessionId: string, silent = false) =>
		client.get<ChatQueueResponse>(
			'/chat/queue',
			{
				agent_id: agentId,
				session_id: sessionId,
			},
			{ silent },
		),

	/**
	 * Replace one item that has not started execution.
	 *
	 * @param itemId - Stable id of the pending item.
	 * @param body - Target ownership fields and replacement input.
	 * @returns Complete queue snapshot after the update.
	 */
	updateQueued: (
		itemId: string,
		body: { agent_id: string; session_id: string; input: Msg | Msg[] },
	) => client.patch<ChatQueueResponse>(`/chat/queue/${encodeURIComponent(itemId)}`, body),

	/**
	 * Delete one item that has not started execution.
	 *
	 * @param itemId - Stable id of the pending item.
	 * @param agentId - Agent that owns the target session.
	 * @param sessionId - Session whose item should be deleted.
	 * @returns Complete queue snapshot after deletion.
	 */
	deleteQueued: (itemId: string, agentId: string, sessionId: string) =>
		client.delete<ChatQueueResponse>(`/chat/queue/${encodeURIComponent(itemId)}`, {
			agent_id: agentId,
			session_id: sessionId,
		}),

	/**
	 * Apply an exact permutation of the current pending queue.
	 *
	 * @param agentId - Agent that owns the target session.
	 * @param sessionId - Session whose FIFO should be reordered.
	 * @param itemIds - All pending item ids in the desired order.
	 * @returns Complete queue snapshot after reordering.
	 */
	reorderQueued: (agentId: string, sessionId: string, itemIds: string[]) =>
		client.patch<ChatQueueResponse>('/chat/queue/order', {
			agent_id: agentId,
			session_id: sessionId,
			item_ids: itemIds,
		}),
};
