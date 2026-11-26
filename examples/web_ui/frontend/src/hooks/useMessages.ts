import { EventType } from '@agentscope-ai/agentscope/event';
import type {
	AgentEvent,
	CustomEvent,
	DataBlockStartEvent,
	DataBlockDeltaEvent,
	DataBlockEndEvent,
	ExternalExecutionResultEvent,
	ReplyStartEvent,
	ToolResultEndEvent,
	UserConfirmResultEvent,
} from '@agentscope-ai/agentscope/event';
import { AssistantMsg, getContentBlocks, UserMsg } from '@agentscope-ai/agentscope/message';
import type { Msg, ContentBlock } from '@agentscope-ai/agentscope/message';
import type { ToolCallBlock, ToolResultBlock } from '@agentscope-ai/agentscope/message';
import { useState, useCallback, useRef, useEffect, useMemo, useLayoutEffect } from 'react';
import { toast } from 'sonner';

import { chatApi, sessionApi, takeFreshlyCreated } from '@/api';
import type { ChatQueueItem } from '@/api/chat';
import { ApiError } from '@/api/client';
import { useAudioManager } from '@/context/AudioContext';
import {
	getQueueMessages,
	type ChatInputStartedValue,
	type ChatInputTerminalValue,
	useChatQueue,
} from '@/hooks/useChatQueue';
import { useTranslation } from '@/i18n/useI18n';
import { ASK_USER_TOOL_NAME, formatAskUserOutput, parseAskUserResult } from '@/lib/ask-user';
import type { ClientExternalToolResult } from '@/lib/client-external-tools';
import { appendMessageEvent, confirmationId } from '@/lib/message-events';
import { getPendingHitlCalls } from '@/lib/pendingToolCalls';

/**
 * One pending subagent HITL request, projected from a team *member*
 * session onto its *leader* session so the leader UI can render and
 * resolve it. Mirrors the Python payload written by
 * ``SubagentHitlProjector`` and pushed/replayed as a ``CustomEvent``
 * (``name="subagent_require_user_confirm"``).
 */
export type SubagentHitlEntry = {
	worker_session_id: string;
	worker_agent_id: string;
	worker_agent_name: string;
	reply_id: string;
	event_type: 'require_user_confirm' | 'require_external_execution';
	/** The original confirmation or external execution request. */
	event: { id: string; tool_calls?: ToolCallBlock[] } & Record<string, unknown>;
	created_at: string;
};

function postToolReceipt(
	agentId: string,
	sessionId: string,
	input: UserConfirmResultEvent | ExternalExecutionResultEvent,
	member?: SubagentHitlEntry,
) {
	return chatApi.trigger(
		{ agent_id: agentId, session_id: sessionId, input },
		member && { agent_id: member.worker_agent_id, session_id: member.worker_session_id },
	);
}

type MemberCardKey = Pick<SubagentHitlEntry, 'worker_session_id' | 'reply_id'> & {
	request_id: string;
};
export const memberCardKey = (entry: SubagentHitlEntry | MemberCardKey) =>
	JSON.stringify([
		entry.worker_session_id,
		entry.reply_id,
		'event' in entry ? entry.event.id : entry.request_id,
	]);

/**
 * Lifecycle phase of the reply currently owned by this session.
 *
 * - ``idle`` — no in-flight reply; the send button is enabled.
 * - ``streaming`` — a reply is in progress (either actively producing
 *   events or parked awaiting HITL). The parked-vs-generating
 *   distinction is not tracked here; HITL cards
 *   render themselves from message content when a
 *   ``RequireUserConfirmEvent`` block is present.
 * - ``interrupting`` — the user has requested a stop and we are
 *   waiting for the backend's terminating ``ReplyEndEvent``. Falls back
 *   to ``idle`` after a 10s safety timeout in case the terminating
 *   event never arrives (dropped SSE frame, backend bug, etc.).
 */
export type ReplyPhase = 'idle' | 'streaming' | 'interrupting';

/** Safety fallback: force phase back to idle if REPLY_END is not seen. */
const INTERRUPT_TIMEOUT_MS = 10_000;
const EXTERNAL_RECEIPT_TIMEOUT_MS = 60_000;

/**
 * Manages messages for a single ``(agentId, sessionId)`` pair.
 *
 * Event delivery has two independent channels:
 *
 * - **History** — ``GET /sessions/{sid}/messages`` fetches persisted
 *   ``Msg`` objects (each a complete reply).
 * - **Live stream** — ``GET /sessions/{sid}/stream`` is a long-lived
 *   SSE connection that pushes ``AgentEvent`` deltas as they are
 *   produced by any chat run on this session (user-triggered,
 *   background retrigger, team member message, …).
 *
 * The hook opens the SSE connection immediately after fetching
 * history. Ordinary user input is accepted into the backend FIFO.
 * Human-in-the-loop confirmations use the active realtime control
 * channel during voice mode and ``POST /chat/`` otherwise. Resulting
 * events arrive through the already-open SSE connection.
 *
 * ``phase`` is driven by event content, not HTTP lifecycle: it moves
 * to ``streaming`` on ``ReplyStartEvent`` and back to ``idle`` on
 * ``ReplyEndEvent``. Calling ``interrupt()`` moves it to
 * ``interrupting`` until the terminating ``ReplyEndEvent`` arrives (or
 * a 10s safety timeout fires).
 *
 * @param agentId - The agent whose session to subscribe. ``null`` to
 *   skip.
 * @param sessionId - The session to subscribe. ``null`` to skip.
 * @returns Message/reply state, editable queue state and mutations, HITL
 *   handlers, send/interrupt actions, loading state, and the latest error.
 *   ``loading`` stays true until history for the current session lands.
 */
export function useMessages(
	agentId: string | null,
	sessionId: string | null,
	options?: {
		/**
		 * Called when a ``CUSTOM`` event with ``name="team_updated"``
		 * arrives — the team membership has changed (TeamCreate /
		 * AgentCreate / TeamDelete ran). The typical response is to
		 * refetch the session list so the team sidebar updates.
		 */
		onTeamUpdated?: () => void;
		/**
		 * Called when a ``CUSTOM`` event with ``name="state_updated"``
		 * arrives — agent state (tasks / permission) changed during a
		 * tool call. The ``value`` payload contains the latest
		 * ``tasks_context`` and ``permission_context``.
		 */
		onStateUpdated?: (value: Record<string, unknown>) => void;
		/**
		 * Called when a ``CUSTOM`` event with ``name="session_updated"``
		 * arrives — the session record changed server-side, currently
		 * only when auto-naming replaced its placeholder name. Refetch
		 * the session list to pick the new one up.
		 */
		onSessionUpdated?: () => void;
		/** True when a dedicated realtime transport already plays PCM audio. */
		isRealtimeAudioActive?: () => boolean;
		/** Send a tool confirmation over the active realtime transport. */
		sendRealtimeUserConfirm?: (event: UserConfirmResultEvent) => Promise<void>;
		/** Return an external tool result over the realtime transport. */
		sendRealtimeExternalExecutionResult?: (
			event: ExternalExecutionResultEvent,
		) => Promise<void>;
	},
) {
	const { t } = useTranslation();
	// A new identity for each visit also rejects late responses after A -> B -> A.
	const session = useMemo(() => ({ agentId, sessionId }), [agentId, sessionId]);
	const [missingSession, setMissingSession] = useState<typeof session | null>(null);
	const sessionMissing = missingSession === session;
	const externalSubmissionsRef = useRef(
		new Map<
			string,
			{
				replyId: string;
				sourceSession: string;
				toolCallIds: Set<string>;
				resolve: () => void;
				reject: (error: Error) => void;
			}
		>(),
	);
	const activeSessionRef = useRef<typeof session | null>(null);
	useLayoutEffect(() => {
		activeSessionRef.current = session;
		const externalSubmissions = externalSubmissionsRef.current;
		return () => {
			activeSessionRef.current = null;
			for (const pending of externalSubmissions.values()) {
				pending.reject(new Error('The session closed before the result was acknowledged.'));
			}
			externalSubmissions.clear();
		};
	}, [session]);
	const [msgs, setMsgs] = useState<Msg[]>([]);
	// The (agent, session) pair `msgs` actually belongs to. Loading is
	// derived from it rather than set inside the fetch effect: an effect
	// runs *after* the render that changed `sessionId`, so a flag it owns
	// is still `false` for one frame — long enough to paint the empty
	// state over a session that does have messages.
	const [loadedKey, setLoadedKey] = useState<string | null>(null);
	const [phase, setPhase] = useState<ReplyPhase>('idle');
	// Only a running reply accepts steering. Parked replies retain their
	// streaming phase and currentReplyRef for confirmation and Stop.
	const [activeReplyId, setActiveReplyId] = useState<string | null>(null);
	const [error, setError] = useState<Error | null>(null);
	// Pending subagent HITL cards projected onto this (leader) session.
	const [subagentHitl, setSubagentHitl] = useState<SubagentHitlEntry[]>([]);
	const {
		items: queuedItems,
		visibleCount: queuedCount,
		pendingCount: pendingQueueCount,
		reorderDisabled: queueReorderDisabled,
		addOptimistic,
		acceptOptimistic,
		rollbackOptimistic,
		applyQueueSnapshot,
		startItem,
		finishItem,
		updateQueued,
		deleteQueued,
		steerQueued,
		moveQueued,
		reorderQueued,
	} = useChatQueue(agentId, sessionMissing ? null : sessionId, setError);

	const msgsRef = useRef<Msg[]>([]);
	const currentReplyRef = useRef<Msg | null>(null);
	const ttsConfigurationWarningsRef = useRef(new Set<string>());
	const abortRef = useRef<AbortController | null>(null);
	const rafRef = useRef<number | null>(null);
	// Timer that reverts ``interrupting`` back to ``idle`` if the
	// terminating REPLY_END never arrives (dropped SSE frame, etc.).
	const interruptTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

	const clearInterruptTimer = useCallback(() => {
		if (interruptTimerRef.current !== null) {
			clearTimeout(interruptTimerRef.current);
			interruptTimerRef.current = null;
		}
	}, []);

	const audioManager = useAudioManager();

	const optionsRef = useRef(options);
	useEffect(() => {
		optionsRef.current = options;
	}, [options]);
	const scheduleUpdate = useCallback(() => {
		if (rafRef.current !== null) return;
		rafRef.current = requestAnimationFrame(() => {
			rafRef.current = null;
			setMsgs([...msgsRef.current]);
		});
	}, []);

	// A member form is acknowledged on the worker stream that consumes it.
	const processReceipt = useCallback(
		(event: AgentEvent, sourceSession: string) => {
			if (event.type === EventType.CUSTOM) {
				const custom = event as CustomEvent;
				if (custom.name === 'external_execution_result_rejected') {
					const failure = custom.value as {
						request_id: string;
						reply_id: string;
						tool_call_ids: string[];
						unavailable_tool?: string;
					};
					const pending = externalSubmissionsRef.current.get(failure.request_id);
					if (
						pending?.sourceSession === sourceSession &&
						pending.replyId === failure.reply_id &&
						failure.tool_call_ids.some((id) => pending.toolCallIds.has(id))
					) {
						externalSubmissionsRef.current.delete(failure.request_id);
						const error = new Error(
							failure.unavailable_tool
								? t('chat.externalExecution.restoreTool', {
										name: failure.unavailable_tool,
									})
								: t('chat.externalExecution.rejected'),
						);
						pending.reject(error);
						toast.error(error.message);
					}
				}
			}
			if (event.type === EventType.TOOL_RESULT_END) {
				const result = event as ToolResultEndEvent & { external_request_id?: string };
				for (const [requestId, pending] of externalSubmissionsRef.current) {
					if (
						pending.sourceSession !== sourceSession ||
						pending.replyId !== result.reply_id ||
						!pending.toolCallIds.has(result.tool_call_id)
					)
						continue;
					if (result.external_request_id !== requestId) {
						externalSubmissionsRef.current.delete(requestId);
						const error = new Error(t('chat.externalExecution.notAccepted'));
						pending.reject(error);
						toast.error(error.message);
					} else {
						pending.toolCallIds.delete(result.tool_call_id);
						if (!pending.toolCallIds.size) {
							externalSubmissionsRef.current.delete(requestId);
							pending.resolve();
						}
					}
				}
			} else if (event.type === EventType.REPLY_END) {
				for (const [requestId, pending] of externalSubmissionsRef.current) {
					if (
						pending.sourceSession !== sourceSession ||
						pending.replyId !== event.reply_id
					)
						continue;
					externalSubmissionsRef.current.delete(requestId);
					pending.reject(new Error(t('chat.externalExecution.rejected')));
				}
			}
		},
		[t],
	);

	/** Apply a single AgentEvent to the in-progress reply. */
	const processEvent = useCallback(
		(event: AgentEvent) => {
			if (sessionId) processReceipt(event, sessionId);
			// Custom events are service-layer notifications, not agent
			// reply content — route them to callbacks and skip appendEvent.
			if (event.type === EventType.CUSTOM) {
				const custom = event as CustomEvent;
				if (custom.name === 'tts_error') {
					const failure = custom.value as { stage?: string; reply_id?: string };
					const sessionKey = JSON.stringify([agentId, sessionId]);
					if (failure.stage === 'configuration') {
						if (ttsConfigurationWarningsRef.current.has(sessionKey)) return;
						ttsConfigurationWarningsRef.current.add(sessionKey);
					}
					toast.warning(t('chat.ttsFailed'), {
						id: `tts-error-${sessionKey}-${failure.reply_id ?? custom.id}`,
					});
				} else if (custom.name === 'team_updated') {
					optionsRef.current?.onTeamUpdated?.();
				} else if (custom.name === 'state_updated' && custom.value) {
					optionsRef.current?.onStateUpdated?.(custom.value as Record<string, unknown>);
				} else if (custom.name === 'session_updated') {
					optionsRef.current?.onSessionUpdated?.();
				} else if (custom.name === 'subagent_require_user_confirm') {
					// A team member is asking for confirmation; show (or
					// refresh) its card on this leader view. Dedup by
					// worker, reply and request so sibling cards stay independent.
					const e = custom.value as unknown as SubagentHitlEntry;
					setSubagentHitl((prev) => [
						...prev.filter((x) => memberCardKey(x) !== memberCardKey(e)),
						e,
					]);
				} else if (custom.name === 'subagent_hitl_snapshot') {
					const { pending } = custom.value as {
						pending: MemberCardKey[];
					};
					const keys = new Set(pending.map(memberCardKey));
					setSubagentHitl((prev) =>
						prev.filter((entry) => keys.has(memberCardKey(entry))),
					);
				} else if (custom.name === 'subagent_user_confirm_result') {
					// The member resolved (or its run ended); clear the card.
					const v = custom.value as MemberCardKey;
					setSubagentHitl((prev) =>
						prev.filter((x) => memberCardKey(x) !== memberCardKey(v)),
					);
				} else if (custom.name === 'chat_input_started') {
					const startedItem = startItem(custom.value as ChatInputStartedValue);
					if (startedItem) {
						const existingIds = new Set(msgsRef.current.map((msg) => msg.id));
						msgsRef.current = [
							...msgsRef.current,
							...getQueueMessages([startedItem]).filter(
								(msg) => !existingIds.has(msg.id),
							),
						];
						scheduleUpdate();
					}
				} else if (custom.name === 'chat_queue_changed') {
					const value = custom.value as { items?: ChatQueueItem[] };
					applyQueueSnapshot(value.items ?? []);
				} else if (custom.name === 'chat_input_failed') {
					const value = custom.value as ChatInputTerminalValue;
					finishItem(value);
					const failure = new Error(value.message ?? t('chatQueue.processFailed'));
					clearInterruptTimer();
					currentReplyRef.current = null;
					setPhase('idle');
					setActiveReplyId(null);
					setError(failure);
					toast.error(failure.message);
				} else if (custom.name === 'chat_input_cancelled') {
					const value = custom.value as ChatInputTerminalValue;
					finishItem(value);
					const message = value.message ?? t('chatQueue.processingCancelled');
					clearInterruptTimer();
					currentReplyRef.current = null;
					setPhase('idle');
					setActiveReplyId(null);
					toast.info(message);
				} else if (custom.name === 'chat_input_injected') {
					const value = custom.value as ChatInputTerminalValue;
					finishItem(value);
					toast.success(t('chatQueue.injected'));
				} else if (custom.name === 'chat_input_steer_failed') {
					const value = custom.value as ChatInputTerminalValue;
					toast.error(value.message ?? t('chatQueue.steerFailed'));
				}
				return;
			}
			if (event.type === EventType.REPLY_START) {
				const e = event as ReplyStartEvent;
				// A continuation (the run resuming after a confirmation or an
				// external execution result) re-emits REPLY_START with the
				// *same* reply_id. Re-point at the existing reply instead of
				// appending a second msg under that id: a duplicate would
				// collide on React keys, strand the original bubble in its
				// running state, and hide any still-pending confirmation card
				// — those are read off the tail msg, which would be the empty
				// duplicate.
				const existing = msgsRef.current.find((m) => m.id === e.reply_id);
				if (existing) {
					currentReplyRef.current = existing;
				} else {
					audioManager?.stopAllPlayback();
					const msg =
						e.role === 'user'
							? UserMsg({ id: e.reply_id, name: e.name, content: [] })
							: AssistantMsg({ id: e.reply_id, name: e.name, content: [] });
					msgsRef.current = [...msgsRef.current, msg];
					currentReplyRef.current = msg;
				}
				setActiveReplyId(e.reply_id);
				clearInterruptTimer();
				setPhase('streaming');
			} else {
				// Realtime providers may start the assistant response before the
				// user's final transcription arrives. Route every identified event
				// to its own reply instead of assuming event streams never overlap.
				const reply = event.reply_id
					? currentReplyRef.current?.id === event.reply_id
						? currentReplyRef.current
						: (msgsRef.current.find((message) => message.id === event.reply_id) ?? null)
					: currentReplyRef.current;
				if (reply) {
					if (event.type === EventType.EXTERNAL_EXECUTION_RESULT) {
						// The echo precedes consumption. Older cards may lack START.
						const existing = getContentBlocks(reply, 'tool_result');
						for (const result of event.execution_results) {
							if (!existing.some((r) => r.id === result.id)) {
								appendMessageEvent(reply, {
									type: EventType.TOOL_RESULT_START,
									id: event.id,
									created_at: event.created_at,
									reply_id: event.reply_id,
									tool_call_id: result.id,
									tool_call_name: result.name,
								});
							}
						}
					} else {
						appendMessageEvent(reply, event);
					}
					// ``appendEvent`` mutates in place, which would leave
					// every Msg identical across renders and force the whole
					// list to re-render on each delta. Republish just the
					// reply that changed under a fresh identity, so the
					// memoised bubbles of the other messages can skip the
					// render. Anything holding a Msg reference across events
					// must re-read it from here — ``currentReplyRef`` below,
					// everything else looks the reply up by id.
					const updated = { ...reply, content: [...reply.content] };
					msgsRef.current = msgsRef.current.map((m) => (m === reply ? updated : m));
					if (currentReplyRef.current?.id === reply.id) {
						currentReplyRef.current = updated;
					}
				}
				if (
					event.type === EventType.REQUIRE_USER_CONFIRM ||
					event.type === EventType.REQUIRE_EXTERNAL_EXECUTION
				) {
					setActiveReplyId((active) =>
						!event.reply_id || active === event.reply_id ? null : active,
					);
				}
				if (
					event.type === EventType.REPLY_END &&
					(!event.reply_id ||
						currentReplyRef.current === null ||
						currentReplyRef.current.id === event.reply_id)
				) {
					clearInterruptTimer();
					setPhase('idle');
					setActiveReplyId(null);
					currentReplyRef.current = null;
				}
			}

			// Route streaming audio DataBlocks to the audio manager. They still
			// flow through `appendEvent` above (which builds up `source.data`
			// in the Msg), but MessageBubble reads playback state from the
			// manager so it can show progress and autoplay on completion.
			if (audioManager) {
				if (event.type === EventType.DATA_BLOCK_START) {
					const e = event as DataBlockStartEvent;
					if (e.media_type.startsWith('audio/')) {
						audioManager.start(e.block_id, e.media_type);
					}
				} else if (event.type === EventType.DATA_BLOCK_DELTA) {
					const e = event as DataBlockDeltaEvent;
					if (e.media_type.startsWith('audio/')) {
						audioManager.append(e.block_id, e.data);
					}
				} else if (event.type === EventType.DATA_BLOCK_END) {
					const e = event as DataBlockEndEvent;
					// `end` is a no-op when the block isn't being tracked, so
					// we can call it unconditionally.
					audioManager.end(e.block_id);
				}
			}

			scheduleUpdate();
		},
		[
			processReceipt,
			scheduleUpdate,
			audioManager,
			clearInterruptTimer,
			startItem,
			applyQueueSnapshot,
			finishItem,
			t,
			agentId,
			sessionId,
		],
	);

	// A persisted reply may already be parked on another tool or completed.
	const applyHistory = useCallback((messages: Msg[], isRunning: boolean) => {
		const pending = getPendingHitlCalls(messages)[0];
		const tail = messages.at(-1);
		const active = isRunning && !pending && tail?.role === 'assistant' ? tail : null;
		msgsRef.current = messages;
		currentReplyRef.current = pending
			? (messages.find((message) => message.id === pending.replyId) ?? null)
			: active;
		setActiveReplyId(active?.id ?? null);
		setPhase(isRunning || pending ? 'streaming' : 'idle');
		setMsgs([...messages]);
	}, []);

	const reconcileReceipts = useCallback(
		(messages: Msg[], sourceSession: string) => {
			const submissions = externalSubmissionsRef.current;
			for (const [id, pending] of submissions) {
				if (pending.sourceSession !== sourceSession) continue;
				const reply = messages.find((m) => m.id === pending.replyId);
				const accepted = reply?.metadata?.external_result_receipts as
					| Record<string, string>
					| undefined;
				const terminal = reply
					? getContentBlocks(reply, 'tool_result').filter((r) => r.state !== 'running')
					: [];
				for (const key of pending.toolCallIds) {
					if (accepted?.[key] === id) pending.toolCallIds.delete(key);
				}
				if (!pending.toolCallIds.size) {
					submissions.delete(id);
					pending.resolve();
				} else {
					const lost = [...pending.toolCallIds].some(
						(key) => terminal.some((r) => r.id === key) && accepted?.[key] !== id,
					);
					// An idle worker may still have this receipt queued.
					if (!lost) continue;
					submissions.delete(id);
					const error = new Error(t('chat.externalExecution.notAccepted'));
					pending.reject(error);
					toast.error(error.message);
				}
			}
		},
		[t],
	);

	// ── Lifecycle: fetch history + open SSE stream ──────────────────
	useEffect(() => {
		if (sessionMissing) return;
		setLoadedKey(null);
		msgsRef.current = [];
		currentReplyRef.current = null;
		setMsgs([]);
		setError(null);
		clearInterruptTimer();
		setPhase('idle');
		setActiveReplyId(null);
		setSubagentHitl([]);
		audioManager?.disposeAll();

		if (!agentId || !sessionId) return;

		const controller = new AbortController();
		abortRef.current = controller;
		let retryTimer: ReturnType<typeof setTimeout>;
		let fresh = takeFreshlyCreated(sessionId);
		const submissions = externalSubmissionsRef.current;
		const connect = async () => {
			let cursor: string | null = null;
			try {
				if (!fresh) {
					const history = await sessionApi.messages(sessionId, agentId);
					if (controller.signal.aborted) return;
					applyHistory(history.messages, history.is_running);
					cursor = history.event_cursor;
					reconcileReceipts(history.messages, sessionId);
				}
				fresh = false;
				setLoadedKey(`${agentId}:${sessionId}`);
				for await (const event of sessionApi.streamEvents(
					sessionId,
					agentId,
					controller.signal,
					cursor,
				)) {
					if (controller.signal.aborted) return;
					processEvent(event);
				}
			} catch (e) {
				if (controller.signal.aborted) return;
				setError(e as Error);
				setLoadedKey(`${agentId}:${sessionId}`);
				if (e instanceof ApiError && e.status === 404) {
					setMissingSession(session);
					for (const pending of submissions.values()) pending.reject(e);
					submissions.clear();
					applyHistory([], false);
					setSubagentHitl([]);
					return;
				}
			}
			if (!controller.signal.aborted) {
				retryTimer = setTimeout(() => void connect(), 1000);
			}
		};
		void connect();
		return () => {
			clearTimeout(retryTimer);
			controller.abort();
			abortRef.current = null;
			clearInterruptTimer();
		};
	}, [
		agentId,
		sessionId,
		session,
		sessionMissing,
		reconcileReceipts,
		applyHistory,
		processEvent,
		audioManager,
		clearInterruptTimer,
		t,
	]);

	/**
	 * Send a user message. The first idle input appears in the conversation
	 * immediately (with its matching queue row hidden); later inputs remain
	 * in the editable queue until ``chat_input_started`` arrives.
	 *
	 * @param content - The message content blocks.
	 */
	const send = useCallback(
		async (content: ContentBlock[]) => {
			if (!agentId || !sessionId || activeSessionRef.current !== session) return;

			const userMsg = UserMsg({ name: 'user', content });
			const showInConversationImmediately = phase === 'idle' && pendingQueueCount === 0;
			if (showInConversationImmediately) {
				msgsRef.current = [...msgsRef.current, userMsg];
				scheduleUpdate();
			}
			const optimisticId = addOptimistic(userMsg, showInConversationImmediately);
			try {
				const response = await chatApi.trigger({
					agent_id: agentId,
					session_id: sessionId,
					input: userMsg,
				});
				if (activeSessionRef.current !== session) return;
				acceptOptimistic(optimisticId, response.queue_item_id);
			} catch (e) {
				if (activeSessionRef.current !== session) return;
				// The server never accepted this input. Remove the
				// optimistic queue row and restore the caller's draft.
				rollbackOptimistic(optimisticId, userMsg.id);
				if (showInConversationImmediately) {
					msgsRef.current = msgsRef.current.filter((msg) => msg.id !== userMsg.id);
					scheduleUpdate();
				}
				setError(e as Error);
				throw e;
			}
		},
		[
			agentId,
			sessionId,
			phase,
			pendingQueueCount,
			session,
			scheduleUpdate,
			addOptimistic,
			acceptOptimistic,
			rollbackOptimistic,
		],
	);

	/**
	 * Confirm or deny a tool call (human-in-the-loop). Sends a
	 * ``UserConfirmResultEvent`` through realtime control while voice mode
	 * is active, or through ``POST /chat/`` otherwise. Events arrive via
	 * SSE in both cases.
	 *
	 * @param toolCall - The tool call block to confirm/deny.
	 * @param confirm - Whether the user confirmed.
	 * @param replyId - The reply id the tool call belongs to.
	 * @param rules - Optional permission rules to attach.
	 */
	const onUserConfirm = useCallback(
		async (
			toolCall: ToolCallBlock,
			confirm: boolean,
			replyId: string,
			rules?: ToolCallBlock['suggested_rules'],
		) => {
			if (!agentId || !sessionId) return;

			const reply = msgsRef.current.find((message) => message.id === replyId);
			// Reject a card superseded between render and click.
			if (toolCall.state !== 'asking' || !reply?.content.includes(toolCall)) {
				throw new Error('This confirmation is no longer current.');
			}
			currentReplyRef.current = reply;
			const answer = {
				confirmed: confirm,
				tool_call: toolCall,
				rules: rules ?? null,
				request_id: confirmationId(reply, toolCall.id),
			};

			const event: UserConfirmResultEvent = {
				type: EventType.USER_CONFIRM_RESULT,
				id: crypto.randomUUID(),
				created_at: new Date().toISOString(),
				reply_id: replyId,
				confirm_results: [answer],
			};

			try {
				if (
					optionsRef.current?.isRealtimeAudioActive?.() &&
					optionsRef.current.sendRealtimeUserConfirm
				) {
					await optionsRef.current.sendRealtimeUserConfirm(event);
				} else {
					await postToolReceipt(agentId, sessionId, event);
				}
			} catch (e) {
				setError(e as Error);
				throw e;
			}
		},
		[agentId, sessionId],
	);

	const watchMemberReceipt = useCallback(
		async (member: SubagentHitlEntry, requestId: string, signal: AbortSignal) => {
			const { worker_session_id: sid, worker_agent_id: aid } = member;
			while (!signal.aborted) {
				try {
					const history = await sessionApi.messages(sid, aid);
					if (signal.aborted) return;
					reconcileReceipts(history.messages, sid);
					if (!externalSubmissionsRef.current.has(requestId)) return;
					for await (const update of sessionApi.streamEvents(
						sid,
						aid,
						signal,
						history.event_cursor,
					)) {
						if (signal.aborted) return;
						processReceipt(update, sid);
					}
				} catch (error) {
					if (error instanceof ApiError && error.status === 404) {
						externalSubmissionsRef.current.get(requestId)?.reject(error);
						return;
					}
				}
				if (!signal.aborted) await new Promise((r) => setTimeout(r, 1000));
			}
		},
		[processReceipt, reconcileReceipts],
	);

	const onExternalExecutionResult = useCallback(
		async (event: ExternalExecutionResultEvent, member?: SubagentHitlEntry) => {
			if (!agentId || !sessionId || activeSessionRef.current !== session) return;

			const watcher = new AbortController();
			let timeout: ReturnType<typeof setTimeout> | undefined;
			try {
				// Both transports acknowledge acceptance on the session event stream.
				const receipt = new Promise<void>((resolve, reject) => {
					externalSubmissionsRef.current.set(event.id, {
						replyId: event.reply_id,
						sourceSession: member?.worker_session_id ?? sessionId,
						toolCallIds: new Set(event.execution_results.map((result) => result.id)),
						resolve,
						reject,
					});
				});
				const options = optionsRef.current;
				const send =
					!member &&
					options?.isRealtimeAudioActive?.() &&
					options.sendRealtimeExternalExecutionResult
						? options.sendRealtimeExternalExecutionResult(event)
						: postToolReceipt(agentId, sessionId, event, member);
				if (member) void watchMemberReceipt(member, event.id, watcher.signal);
				await Promise.all([
					receipt,
					send.then(() => {
						const pending = externalSubmissionsRef.current.get(event.id);
						if (!pending) return;
						timeout = setTimeout(() => {
							const error = new Error(t('chat.externalExecution.unconfirmed'));
							pending.reject(error);
							toast.error(error.message);
						}, EXTERNAL_RECEIPT_TIMEOUT_MS);
					}),
				]);
			} catch (e) {
				if (activeSessionRef.current === session) setError(e as Error);
				throw e;
			} finally {
				watcher.abort();
				clearTimeout(timeout);
				externalSubmissionsRef.current.delete(event.id);
			}
		},
		[agentId, sessionId, session, t, watchMemberReceipt],
	);

	/** Return a client-executed tool result and resume the parked reply. */
	const onClientExternalToolResult = useCallback(
		async (
			toolCall: ToolCallBlock,
			result: ClientExternalToolResult,
			replyId: string,
			member?: SubagentHitlEntry,
			state: 'success' | 'interrupted' = 'success',
		) => {
			if (!agentId || !sessionId || activeSessionRef.current !== session) return;

			if (!member)
				currentReplyRef.current = msgsRef.current.find((msg) => msg.id === replyId) ?? null;
			const createdAt = new Date().toISOString();
			const isAnswer = toolCall.name === ASK_USER_TOOL_NAME && state === 'success';
			const nativeResult = isAnswer ? parseAskUserResult(result) : null;
			if (isAnswer && !nativeResult) {
				throw new Error('Invalid AskUser answers');
			}
			const toolResult: ToolResultBlock = {
				type: 'tool_result',
				id: toolCall.id,
				name: toolCall.name,
				output: [
					{
						type: 'text',
						id: crypto.randomUUID(),
						text: nativeResult
							? formatAskUserOutput(nativeResult)
							: typeof result === 'string'
								? result
								: JSON.stringify(result),
						created_at: createdAt,
						finished_at: createdAt,
					},
				],
				state,
				metadata: nativeResult ?? {},
				created_at: createdAt,
				finished_at: createdAt,
			};
			const event: ExternalExecutionResultEvent = {
				type: EventType.EXTERNAL_EXECUTION_RESULT,
				id: crypto.randomUUID(),
				created_at: createdAt,
				reply_id: replyId,
				execution_results: [toolResult],
			};

			await onExternalExecutionResult(event, member);
		},
		[agentId, sessionId, session, onExternalExecutionResult],
	);

	/** Abort the current SSE connection. */
	const abort = useCallback(() => {
		abortRef.current?.abort();
	}, []);

	/**
	 * Request interruption of the in-progress reply (running or parked
	 * on HITL). Optimistically moves ``phase`` to ``interrupting`` so
	 * the UI can disable the Stop button; the phase reverts to
	 * ``idle`` when the backend's terminating ``ReplyEndEvent``
	 * arrives via SSE (or after a 10s safety timeout, in case that
	 * event is lost).
	 *
	 * Backend contract:
	 * - 202: interrupt was accepted (cancel signal broadcast for a
	 *   running reply, or wakeup enqueued for a parked one). The
	 *   resulting ``ReplyEndEvent`` arrives through the SSE stream and
	 *   drives the phase transition.
	 * - Idle sessions are a silent no-op at the agent layer, so
	 *   spamming this callback is safe.
	 */
	const interrupt = useCallback(async () => {
		if (!agentId || !sessionId) return;
		// Only escalate to ``interrupting`` if a reply is actually in
		// flight; if we're already idle (SSE completed just before the
		// click) leave the phase alone.
		setPhase((prev) => (prev === 'streaming' ? 'interrupting' : prev));
		clearInterruptTimer();
		interruptTimerRef.current = setTimeout(() => {
			interruptTimerRef.current = null;
			setPhase((prev) => (prev === 'interrupting' ? 'idle' : prev));
		}, INTERRUPT_TIMEOUT_MS);
		try {
			await sessionApi.interrupt(sessionId, agentId);
		} catch (e) {
			clearInterruptTimer();
			setPhase((prev) => (prev === 'interrupting' ? 'idle' : prev));
			setError(e as Error);
		}
	}, [agentId, sessionId, clearInterruptTimer]);

	/**
	 * Confirm or deny a tool call that a *team member* is awaiting,
	 * from this leader view (design §3.6 — backend routing).
	 *
	 * The leader endpoint routes the receipt to the worker. The worker's
	 * tool selection supplies the definitions needed to resume its call.
	 *
	 * @param entry - The pending subagent HITL entry being resolved.
	 * @param toolCall - The tool call block to confirm/deny.
	 * @param confirm - Whether the user confirmed.
	 * @param rules - Optional permission rules to attach.
	 */
	const onSubagentConfirm = useCallback(
		async (
			entry: SubagentHitlEntry,
			toolCall: ToolCallBlock,
			confirm: boolean,
			rules?: ToolCallBlock['suggested_rules'],
		) => {
			if (!agentId || !sessionId) return;

			const answer = {
				confirmed: confirm,
				tool_call: toolCall,
				rules: rules ?? null,
				request_id: entry.event.id,
			};
			const event: UserConfirmResultEvent = {
				type: EventType.USER_CONFIRM_RESULT,
				id: crypto.randomUUID(),
				created_at: new Date().toISOString(),
				reply_id: entry.reply_id, // worker's reply_id; backend maps it
				confirm_results: [answer],
			};

			try {
				await postToolReceipt(agentId, sessionId, event, entry);
			} catch (e) {
				setError(e as Error);
				// Rethrow so the card can re-enable itself and be retried.
				throw e;
			}
		},
		[agentId, sessionId],
	);

	const currentKey = agentId !== null && sessionId !== null ? `${agentId}:${sessionId}` : null;
	const ownsConversation = currentKey !== null && loadedKey === currentKey;
	const loading = currentKey !== null && !ownsConversation;

	return {
		msgs: ownsConversation ? msgs : [],
		loading,
		queuedItems: ownsConversation ? queuedItems : [],
		queuedCount: ownsConversation ? queuedCount : 0,
		queueReorderDisabled: !ownsConversation || queueReorderDisabled,
		updateQueued,
		deleteQueued,
		moveQueued,
		reorderQueued,
		phase: ownsConversation ? phase : ('idle' as ReplyPhase),
		error: ownsConversation ? error : null,
		activeReplyId: ownsConversation && phase === 'streaming' ? activeReplyId : null,
		steerQueued,
		send,
		onUserConfirm,
		onClientExternalToolResult,
		onSubagentConfirm,
		subagentHitl: ownsConversation ? subagentHitl : [],
		abort,
		interrupt,
	};
}
