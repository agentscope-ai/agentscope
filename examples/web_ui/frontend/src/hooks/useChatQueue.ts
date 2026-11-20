import type { ContentBlock, Msg, TextBlock } from '@agentscope-ai/agentscope/message';
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';

import { chatApi } from '@/api';
import type { ChatQueueItem } from '@/api/chat';
import { ApiError } from '@/api/client';

const QUEUE_REFRESH_INTERVAL_MS = 15_000;
const OPTIMISTIC_ITEM_PREFIX = 'optimistic:';

export interface ChatInputStartedValue {
	queue_item_id?: string;
	message_ids?: string[];
	queue_item?: ChatQueueItem;
}

export interface ChatInputTerminalValue {
	queue_item_id?: string;
	message_ids?: string[];
	message?: string;
}

type LocalInputOutcome = 'finished' | 'removed';

interface LocalChatInput {
	message: Msg;
	optimisticId: string;
	queueId: string | null;
	outcomes: Map<string, LocalInputOutcome | Msg>;
}

/** Return every message carried by queue items in FIFO order. */
export const getQueueMessages = (items: ChatQueueItem[]): Msg[] =>
	items.flatMap((item) => (Array.isArray(item.input) ? item.input : [item.input]));

/**
 * Owns the editable pending-turn queue for one chat session.
 *
 * The hook keeps server snapshots, optimistic submissions, periodic repair
 * polling, and queue mutations out of the reply-stream state machine.
 */
export function useChatQueue(
	agentId: string | null,
	sessionId: string | null,
	onError: (error: Error) => void,
) {
	const [items, setItems] = useState<ChatQueueItem[]>([]);
	const [conversationMessageIds, setConversationMessageIds] = useState<Set<string>>(new Set());
	const itemsRef = useRef<ChatQueueItem[]>([]);
	const conversationMessageIdsRef = useRef<Set<string>>(new Set());
	// Keep receipt identity even when a queue snapshot omits the local row.
	const localInputs = useRef(new Map<string, LocalChatInput>());
	const snapshotVersionRef = useRef(0);
	const refreshSequenceRef = useRef(0);
	// Object identity distinguishes A -> B -> A from the original A visit.
	const session = useMemo(() => ({ agentId, sessionId }), [agentId, sessionId]);
	const activeSessionRef = useRef<typeof session | null>(null);

	useLayoutEffect(() => {
		activeSessionRef.current = session;
		return () => {
			activeSessionRef.current = null;
		};
	}, [session]);

	const updateItems = useCallback((next: ChatQueueItem[]) => {
		snapshotVersionRef.current += 1;
		itemsRef.current = next;
		setItems(next);
	}, []);

	const updateConversationMessageIds = useCallback((update: (ids: Set<string>) => void) => {
		const next = new Set(conversationMessageIdsRef.current);
		update(next);
		conversationMessageIdsRef.current = next;
		setConversationMessageIds(next);
	}, []);

	const reset = useCallback(() => {
		localInputs.current.clear();
		updateItems([]);
		updateConversationMessageIds((ids) => ids.clear());
	}, [updateConversationMessageIds, updateItems]);

	/** Apply an authoritative snapshot without erasing unaccepted local turns. */
	const applyQueueSnapshot = useCallback(
		(serverItems: ChatQueueItem[]) => {
			const optimisticItems = itemsRef.current.filter((item) =>
				item.id.startsWith(OPTIMISTIC_ITEM_PREFIX),
			);
			const serverMessageIds = new Set(
				getQueueMessages(serverItems).map((message) => message.id),
			);
			const unmatchedOptimisticItems = optimisticItems.filter(
				(item) =>
					!getQueueMessages([item]).some((message) => serverMessageIds.has(message.id)),
			);
			updateItems([...serverItems, ...unmatchedOptimisticItems]);
		},
		[updateItems],
	);

	const refreshQueue = useCallback(
		async (signal?: AbortSignal) => {
			if (!agentId || !sessionId || activeSessionRef.current !== session) return;
			const requestId = ++refreshSequenceRef.current;
			const snapshotVersion = snapshotVersionRef.current;
			try {
				const response = await chatApi.queue(agentId, sessionId, true, {
					signal,
					timeoutMs: 15_000,
				});
				if (
					signal?.aborted ||
					activeSessionRef.current !== session ||
					requestId !== refreshSequenceRef.current ||
					snapshotVersion !== snapshotVersionRef.current
				) {
					return;
				}
				applyQueueSnapshot(response.items);
			} catch (error) {
				if (signal && !signal.aborted) throw error;
				// Focus, reconnect, or the next interval retries transient failures.
			}
		},
		[agentId, sessionId, session, applyQueueSnapshot],
	);

	/** Keep provisional bubbles for inputs still in flight or in the restored queue. */
	const restoreConversation = useCallback(
		async (history: Msg[], signal: AbortSignal) => {
			await refreshQueue(signal);
			if (signal.aborted) return history;
			const saved = new Set(history.map((msg) => msg.id));
			const pending: Msg[] = [];
			const retired = new Set<string>();
			for (const [id, local] of localInputs.current) {
				const row = itemsRef.current.find((item) => item.id === local.queueId);
				const stillQueued = row && getQueueMessages([row]).some((msg) => msg.id === id);
				if (saved.has(id) || (local.queueId && !stillQueued)) {
					localInputs.current.delete(id);
					retired.add(local.optimisticId);
				} else if (conversationMessageIdsRef.current.has(id)) pending.push(local.message);
			}
			if (retired.size) updateItems(itemsRef.current.filter((item) => !retired.has(item.id)));
			updateConversationMessageIds((ids) => {
				ids.clear();
				for (const msg of pending) ids.add(msg.id);
			});
			return [...history, ...pending];
		},
		[refreshQueue, updateConversationMessageIds, updateItems],
	);

	const applyMutationSnapshot = useCallback(
		(serverItems: ChatQueueItem[], snapshotVersion: number) => {
			if (activeSessionRef.current !== session) return;
			if (snapshotVersion !== snapshotVersionRef.current) {
				// An SSE event or another response already changed the queue.
				// Fetch current state instead of resurrecting an older snapshot.
				void refreshQueue();
				return;
			}
			applyQueueSnapshot(serverItems);
		},
		[session, applyQueueSnapshot, refreshQueue],
	);

	useEffect(() => {
		reset();
		if (!agentId || !sessionId) return;
		void refreshQueue();
		const refreshTimer = window.setInterval(
			() => void refreshQueue(),
			QUEUE_REFRESH_INTERVAL_MS,
		);
		const refreshOnFocus = () => void refreshQueue();
		window.addEventListener('focus', refreshOnFocus);
		window.addEventListener('online', refreshOnFocus);

		return () => {
			window.clearInterval(refreshTimer);
			window.removeEventListener('focus', refreshOnFocus);
			window.removeEventListener('online', refreshOnFocus);
		};
	}, [agentId, sessionId, refreshQueue, reset]);

	const addOptimistic = useCallback(
		(message: Msg, shownInConversation: boolean): string => {
			const optimisticId = `${OPTIMISTIC_ITEM_PREFIX}${crypto.randomUUID()}`;
			localInputs.current.set(message.id, {
				message,
				optimisticId,
				queueId: null,
				outcomes: new Map(),
			});
			if (shownInConversation) {
				updateConversationMessageIds((ids) => ids.add(message.id));
			}
			updateItems([
				...itemsRef.current,
				{
					id: optimisticId,
					created_at: message.created_at,
					input: message,
					state: 'queued',
					error: null,
				},
			]);
			return optimisticId;
		},
		[updateConversationMessageIds, updateItems],
	);

	const rollbackOptimistic = useCallback(
		(optimisticId: string, messageId: string) => {
			localInputs.current.delete(messageId);
			updateItems(itemsRef.current.filter((item) => item.id !== optimisticId));
			updateConversationMessageIds((ids) => ids.delete(messageId));
		},
		[updateConversationMessageIds, updateItems],
	);

	/** Retire only the local turn whose receipt matches the server queue item. */
	const retireItem = useCallback(
		(
			{ queue_item_id: queueId, message_ids = [] }: ChatInputTerminalValue,
			outcome: LocalInputOutcome,
			startedMessages: Msg[] = [],
		) => {
			const rows = new Set(queueId ? [queueId] : []);
			const provisional = new Set<Msg>();
			for (const [id, local] of localInputs.current) {
				if (queueId && !local.queueId) {
					// An edit can replace every message ID before our POST returns.
					const replaced = message_ids.length > 0 && !message_ids.includes(id);
					const started = startedMessages.find((msg) => msg.id === id);
					if (started || !local.outcomes.has(queueId) || outcome === 'removed') {
						local.outcomes.set(queueId, started ?? (replaced ? 'removed' : outcome));
					}
					continue;
				}
				if (queueId ? queueId !== local.queueId : !message_ids.includes(id)) continue;
				localInputs.current.delete(id);
				rows.add(local.optimisticId);
				if (local.queueId) rows.add(local.queueId);
				if (conversationMessageIdsRef.current.has(id)) provisional.add(local.message);
			}
			updateItems(itemsRef.current.filter((item) => !rows.has(item.id)));
			if (provisional.size) {
				updateConversationMessageIds((ids) => {
					for (const msg of provisional) ids.delete(msg.id);
				});
			}
			return provisional;
		},
		[updateConversationMessageIds, updateItems],
	);

	const startItem = useCallback(
		(value: ChatInputStartedValue) => {
			const item =
				value.queue_item ?? itemsRef.current.find((row) => row.id === value.queue_item_id);
			const provisional = retireItem(value, 'finished', item ? getQueueMessages([item]) : []);
			return { item, provisional };
		},
		[retireItem],
	);

	const acceptOptimistic = useCallback(
		(messageId: string, queueItemId: string) => {
			const local = localInputs.current.get(messageId);
			if (!local || local.queueId === queueItemId) return;
			local.queueId = queueItemId;
			const outcome = local.outcomes.get(queueItemId);
			local.outcomes.clear();
			if (outcome === 'removed') return outcome;
			if (outcome) {
				retireItem({ queue_item_id: queueItemId, message_ids: [messageId] }, 'finished');
				return outcome;
			}
			const { optimisticId } = local;
			updateItems(
				itemsRef.current.map((item) =>
					item.id === optimisticId ? { ...item, id: queueItemId } : item,
				),
			);
		},
		[retireItem, updateItems],
	);

	const updateQueued = useCallback(
		async (itemId: string, text: string) => {
			if (!agentId || !sessionId || activeSessionRef.current !== session) return;
			const item = itemsRef.current.find((candidate) => candidate.id === itemId);
			if (!item || Array.isArray(item.input)) return;

			let replacedText = false;
			const content: ContentBlock[] = [];
			for (const block of item.input.content) {
				if (block.type !== 'text') {
					content.push(block);
				} else if (!replacedText) {
					replacedText = true;
					content.push({ ...block, text });
				}
			}
			if (!replacedText) {
				const now = new Date().toISOString();
				const textBlock: TextBlock = {
					id: crypto.randomUUID(),
					type: 'text',
					text,
					created_at: now,
					finished_at: now,
				};
				content.unshift(textBlock);
			}

			const snapshotVersion = snapshotVersionRef.current;
			try {
				const response = await chatApi.updateQueued(itemId, {
					agent_id: agentId,
					session_id: sessionId,
					input: { ...item.input, content },
				});
				applyMutationSnapshot(response.items, snapshotVersion);
			} catch (error) {
				if (activeSessionRef.current === session) onError(error as Error);
				throw error;
			}
		},
		[agentId, sessionId, session, applyMutationSnapshot, onError],
	);

	const deleteQueued = useCallback(
		async (itemId: string) => {
			if (!agentId || !sessionId || activeSessionRef.current !== session) return;
			const snapshotVersion = snapshotVersionRef.current;
			try {
				const response = await chatApi.deleteQueued(itemId, agentId, sessionId);
				applyMutationSnapshot(response.items, snapshotVersion);
			} catch (error) {
				if (activeSessionRef.current === session) onError(error as Error);
				throw error;
			}
		},
		[agentId, sessionId, session, applyMutationSnapshot, onError],
	);

	const steerQueued = useCallback(
		async (itemId: string, replyId: string) => {
			if (!agentId || !sessionId || activeSessionRef.current !== session) return;
			const previousItem = itemsRef.current.find((item) => item.id === itemId);
			if (!previousItem) return;
			updateItems(
				itemsRef.current.map((item) =>
					item.id === itemId
						? { ...item, state: 'steering' as const, error: null }
						: item,
				),
			);
			const snapshotVersion = snapshotVersionRef.current;
			try {
				const response = await chatApi.steerQueued(itemId, agentId, sessionId, replyId);
				applyMutationSnapshot(response.items, snapshotVersion);
			} catch (error) {
				if (activeSessionRef.current === session) {
					if (snapshotVersion === snapshotVersionRef.current) {
						updateItems(
							itemsRef.current.map((item) =>
								item.id === itemId ? previousItem : item,
							),
						);
					}
					void refreshQueue();
					onError(error as Error);
				}
				throw error;
			}
		},
		[agentId, sessionId, session, applyMutationSnapshot, onError, updateItems, refreshQueue],
	);

	const reorderQueued = useCallback(
		async (visibleItemIds: string[]) => {
			if (!agentId || !sessionId || activeSessionRef.current !== session) return;
			const current = itemsRef.current;
			if (current.some((item) => item.id.startsWith(OPTIMISTIC_ITEM_PREFIX))) return;

			const visibleIds = current
				.filter(
					(item) =>
						!getQueueMessages([item]).some((message) =>
							conversationMessageIdsRef.current.has(message.id),
						),
				)
				.map((item) => item.id);
			const visibleIdSet = new Set(visibleIds);
			if (
				visibleItemIds.length !== visibleIds.length ||
				new Set(visibleItemIds).size !== visibleIds.length ||
				visibleItemIds.some((itemId) => !visibleIdSet.has(itemId))
			) {
				return;
			}

			let visibleIndex = 0;
			const fullItemIds = current.map((item) =>
				visibleIdSet.has(item.id) ? visibleItemIds[visibleIndex++] : item.id,
			);
			const snapshotVersion = snapshotVersionRef.current;
			try {
				const response = await chatApi.reorderQueued(agentId, sessionId, fullItemIds);
				applyMutationSnapshot(response.items, snapshotVersion);
			} catch (error) {
				if (activeSessionRef.current === session) {
					onError(error as Error);
					if (error instanceof ApiError && error.status === 409) {
						await refreshQueue();
					}
				}
				throw error;
			}
		},
		[agentId, sessionId, session, applyMutationSnapshot, onError, refreshQueue],
	);

	const moveQueued = useCallback(
		async (itemId: string, direction: -1 | 1) => {
			const current = itemsRef.current.filter(
				(item) =>
					!getQueueMessages([item]).some((message) =>
						conversationMessageIdsRef.current.has(message.id),
					),
			);
			const index = current.findIndex((item) => item.id === itemId);
			const nextIndex = index + direction;
			if (index < 0 || nextIndex < 0 || nextIndex >= current.length) return;
			const reordered = [...current];
			[reordered[index], reordered[nextIndex]] = [reordered[nextIndex], reordered[index]];
			await reorderQueued(reordered.map((item) => item.id));
		},
		[reorderQueued],
	);

	const visibleItems = useMemo(
		() =>
			items.filter(
				(item) =>
					!getQueueMessages([item]).some((message) =>
						conversationMessageIds.has(message.id),
					),
			),
		[conversationMessageIds, items],
	);
	const reorderDisabled = useMemo(
		() =>
			items.some(
				(item) => item.id.startsWith(OPTIMISTIC_ITEM_PREFIX) || item.state === 'steering',
			),
		[items],
	);

	return {
		items: visibleItems,
		visibleCount: visibleItems.filter((item) => item.state !== 'steering').length,
		pendingCount: items.length,
		// A queue refresh can omit a claimed input before its start event.
		hasPendingInput: items.length > 0 || conversationMessageIds.size > 0,
		reorderDisabled,
		addOptimistic,
		acceptOptimistic,
		rollbackOptimistic,
		applyQueueSnapshot,
		restoreConversation,
		startItem,
		retireItem,
		updateQueued,
		deleteQueued,
		steerQueued,
		moveQueued,
		reorderQueued,
	};
}
