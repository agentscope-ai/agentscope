import type { ContentBlock, Msg, TextBlock } from '@agentscope-ai/agentscope/message';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

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
	const snapshotVersionRef = useRef(0);

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

	useEffect(() => {
		reset();
		if (!agentId || !sessionId) return;

		let cancelled = false;
		let requestSequence = 0;
		const refreshQueue = async () => {
			const requestId = ++requestSequence;
			const snapshotVersion = snapshotVersionRef.current;
			try {
				const response = await chatApi.queue(agentId, sessionId, true);
				if (
					cancelled ||
					requestId !== requestSequence ||
					snapshotVersion !== snapshotVersionRef.current
				) {
					return;
				}
				applyQueueSnapshot(response.items);
			} catch {
				// This is a repair path. Focus, reconnect, or the next interval
				// retries transient failures without showing duplicate toasts.
			}
		};

		void refreshQueue();
		const refreshTimer = window.setInterval(
			() => void refreshQueue(),
			QUEUE_REFRESH_INTERVAL_MS,
		);
		const refreshOnFocus = () => void refreshQueue();
		window.addEventListener('focus', refreshOnFocus);
		window.addEventListener('online', refreshOnFocus);

		return () => {
			cancelled = true;
			window.clearInterval(refreshTimer);
			window.removeEventListener('focus', refreshOnFocus);
			window.removeEventListener('online', refreshOnFocus);
		};
	}, [agentId, sessionId, applyQueueSnapshot, reset]);

	const addOptimistic = useCallback(
		(message: Msg, shownInConversation: boolean): string => {
			const optimisticId = `${OPTIMISTIC_ITEM_PREFIX}${crypto.randomUUID()}`;
			if (shownInConversation) {
				updateConversationMessageIds((ids) => ids.add(message.id));
			}
			updateItems([
				...itemsRef.current,
				{
					id: optimisticId,
					created_at: message.created_at,
					input: message,
				},
			]);
			return optimisticId;
		},
		[updateConversationMessageIds, updateItems],
	);

	const acceptOptimistic = useCallback(
		(optimisticId: string, queueItemId: string | null) => {
			if (!queueItemId) return;
			updateItems(
				itemsRef.current.map((item) =>
					item.id === optimisticId ? { ...item, id: queueItemId } : item,
				),
			);
		},
		[updateItems],
	);

	const rollbackOptimistic = useCallback(
		(optimisticId: string, messageId: string) => {
			updateItems(itemsRef.current.filter((item) => item.id !== optimisticId));
			updateConversationMessageIds((ids) => ids.delete(messageId));
		},
		[updateConversationMessageIds, updateItems],
	);

	/** Remove a started item and return the message payload for conversation UI. */
	const startItem = useCallback(
		(value: ChatInputStartedValue): ChatQueueItem | undefined => {
			const startedIds = new Set(value.message_ids ?? []);
			updateConversationMessageIds((ids) => {
				for (const messageId of startedIds) ids.delete(messageId);
			});
			const localItem = itemsRef.current.find(
				(item) =>
					item.id === value.queue_item_id ||
					(item.id.startsWith(OPTIMISTIC_ITEM_PREFIX) &&
						getQueueMessages([item]).some((message) => startedIds.has(message.id))),
			);
			updateItems(
				itemsRef.current.filter(
					(item) =>
						item.id !== value.queue_item_id &&
						!(
							item.id.startsWith(OPTIMISTIC_ITEM_PREFIX) &&
							getQueueMessages([item]).some((message) => startedIds.has(message.id))
						),
				),
			);
			return value.queue_item ?? localItem;
		},
		[updateConversationMessageIds, updateItems],
	);

	const finishItem = useCallback(
		(value: ChatInputTerminalValue) => {
			const terminalIds = new Set(value.message_ids ?? []);
			updateItems(
				itemsRef.current.filter(
					(item) =>
						item.id !== value.queue_item_id &&
						!getQueueMessages([item]).some((message) => terminalIds.has(message.id)),
				),
			);
			updateConversationMessageIds((ids) => {
				for (const messageId of terminalIds) ids.delete(messageId);
			});
		},
		[updateConversationMessageIds, updateItems],
	);

	const updateQueued = useCallback(
		async (itemId: string, text: string) => {
			if (!agentId || !sessionId) return;
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

			try {
				const response = await chatApi.updateQueued(itemId, {
					agent_id: agentId,
					session_id: sessionId,
					input: { ...item.input, content },
				});
				applyQueueSnapshot(response.items);
			} catch (error) {
				onError(error as Error);
				throw error;
			}
		},
		[agentId, sessionId, applyQueueSnapshot, onError],
	);

	const deleteQueued = useCallback(
		async (itemId: string) => {
			if (!agentId || !sessionId) return;
			try {
				const response = await chatApi.deleteQueued(itemId, agentId, sessionId);
				applyQueueSnapshot(response.items);
			} catch (error) {
				onError(error as Error);
				throw error;
			}
		},
		[agentId, sessionId, applyQueueSnapshot, onError],
	);

	const reorderQueued = useCallback(
		async (visibleItemIds: string[]) => {
			if (!agentId || !sessionId) return;
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
			try {
				const response = await chatApi.reorderQueued(agentId, sessionId, fullItemIds);
				applyQueueSnapshot(response.items);
			} catch (error) {
				onError(error as Error);
				if (error instanceof ApiError && error.status === 409) {
					try {
						const response = await chatApi.queue(agentId, sessionId, true);
						applyQueueSnapshot(response.items);
					} catch {
						// Keep the original conflict as the mutation result. Periodic
						// repair will retry a failed refresh.
					}
				}
				throw error;
			}
		},
		[agentId, sessionId, applyQueueSnapshot, onError],
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
		() => items.some((item) => item.id.startsWith(OPTIMISTIC_ITEM_PREFIX)),
		[items],
	);

	return {
		items: visibleItems,
		visibleCount: visibleItems.length,
		pendingCount: items.length,
		reorderDisabled,
		addOptimistic,
		acceptOptimistic,
		rollbackOptimistic,
		applyQueueSnapshot,
		startItem,
		finishItem,
		updateQueued,
		deleteQueued,
		moveQueued,
		reorderQueued,
	};
}
