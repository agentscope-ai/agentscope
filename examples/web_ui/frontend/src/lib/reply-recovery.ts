import type { AgentEvent } from '@agentscope-ai/agentscope/event';
import { AssistantMsg, type Msg } from '@agentscope-ai/agentscope/message';

/** Resolve against the published snapshot, never a stale object from an older fetch. */
export function findReplyById(messages: Msg[], replyId?: string | null): Msg | null {
	return replyId ? (messages.find((message) => message.id === replyId) ?? null) : null;
}

/** Mark a reply whose start was evicted. No missing content or parameters are inferred. */
export function createPartialReply(event: AgentEvent): Msg | null {
	if (!('reply_id' in event) || !event.reply_id || event.type === 'REPLY_START') return null;
	return AssistantMsg({
		id: event.reply_id,
		name: 'assistant',
		created_at: event.created_at,
		content: [],
		metadata: { partial_replay: true },
	});
}

/** Canonical assistant state replaces replay state; only unsaved user inputs are retained. */
export function mergeHistoryWithPendingUsers(
	history: Msg[],
	pendingUsers: ReadonlyMap<string, Msg>,
): Msg[] {
	const ids = new Set(history.map((message) => message.id));
	return [
		...history,
		...[...pendingUsers.values()].filter(
			(message) => message.role === 'user' && !ids.has(message.id),
		),
	];
}

export function canonicalRepliesReady(messages: Msg[], replyIds: ReadonlySet<string>): boolean {
	return [...replyIds].every((id) => {
		const message = findReplyById(messages, id);
		return Boolean(message?.finished_at) && message?.metadata?.partial_replay !== true;
	});
}

/** Both successful waits and aborts settle, releasing their timer and abort listener. */
export function waitForAbortableDelay(ms: number, signal: AbortSignal): Promise<void> {
	return new Promise((resolve, reject) => {
		if (signal.aborted) {
			reject(new DOMException('Aborted', 'AbortError'));
			return;
		}
		const onAbort = () => {
			clearTimeout(timer);
			signal.removeEventListener('abort', onAbort);
			reject(new DOMException('Aborted', 'AbortError'));
		};
		const timer = setTimeout(() => {
			signal.removeEventListener('abort', onAbort);
			resolve();
		}, ms);
		signal.addEventListener('abort', onAbort, { once: true });
	});
}

/** Called with the old stream closed, so a snapshot and its cursor form one recovery boundary. */
export async function loadReplyCheckpoint<T extends { messages: Msg[] }>(
	load: () => Promise<T>,
	replyIds: ReadonlySet<string>,
	signal: AbortSignal,
	delaysMs: number[] = [0, 100, 250, 500, 1_000],
): Promise<T> {
	let snapshot: T | undefined;
	for (const delay of delaysMs.length ? delaysMs : [0]) {
		signal.throwIfAborted();
		if (delay > 0) await waitForAbortableDelay(delay, signal);
		snapshot = await load();
		signal.throwIfAborted();
		if (canonicalRepliesReady(snapshot.messages, replyIds)) return snapshot;
	}
	// Keep the displayed partial reply intact when persistence is slower than
	// this bounded pass. The connection loop can retry without applying an
	// incomplete snapshot or forgetting the reply that still needs recovery.
	throw new Error('The completed reply is not yet available in saved history.');
}
