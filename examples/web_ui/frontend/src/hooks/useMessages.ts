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
import { AssistantMsg, UserMsg } from '@agentscope-ai/agentscope/message';
import type { Msg, ContentBlock } from '@agentscope-ai/agentscope/message';
import type { ToolCallBlock, ToolResultBlock } from '@agentscope-ai/agentscope/message';
import { useState, useCallback, useRef, useEffect, useMemo, useLayoutEffect } from 'react';
import { toast } from 'sonner';

import { chatApi, sessionApi, takeFreshlyCreated } from '@/api';
import type { ChatQueueItem } from '@/api/chat';
import type { StreamEventsObserver } from '@/api/session';
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
import { appendMessageEvent } from '@/lib/message-events';
import { getPendingHitlCalls } from '@/lib/pendingToolCalls';
import {
	createPartialReply,
	findReplyById,
	loadReplyCheckpoint,
	mergeHistoryWithPendingUsers,
	waitForAbortableDelay,
} from '@/lib/reply-recovery';

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
	/** The original ``RequireUserConfirmEvent`` payload (serialized). */
	event: { tool_calls?: ToolCallBlock[] } & Record<string, unknown>;
	created_at: string;
};

const hitlKey = (e: { worker_session_id: string; reply_id: string }) =>
	`${e.worker_session_id}:${e.reply_id}`;

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

export type RunStage =
	| 'preparing'
	| 'model'
	| 'tool'
	| 'awaiting_confirmation'
	| 'awaiting_external_result'
	| 'finalizing'
	| 'reconnecting';

export type StreamConnectionState = 'connecting' | 'connected' | 'reconnecting' | 'disconnected';

/** Safety fallback: force phase back to idle if REPLY_END is not seen. */
const INTERRUPT_TIMEOUT_MS = 10_000;

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
	const externalSubmissionsRef = useRef(
		new Map<
			string,
			{
				replyId: string;
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
	const [runStage, setRunStage] = useState<RunStage | null>(null);
	const [activeToolName, setActiveToolName] = useState<string | null>(null);
	const [connectionState, setConnectionState] = useState<StreamConnectionState>('disconnected');
	const [lastActivity, setLastActivity] = useState<string | null>(null);
	const [streamError, setStreamError] = useState<Error | null>(null);
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
	} = useChatQueue(agentId, sessionId, setError);

	const msgsRef = useRef<Msg[]>([]);
	const currentReplyRef = useRef<Msg | null>(null);
	const ttsConfigurationWarningsRef = useRef(new Set<string>());
	const abortRef = useRef<AbortController | null>(null);
	const rafRef = useRef<number | null>(null);
	// Timer that reverts ``interrupting`` back to ``idle`` if the
	// terminating REPLY_END never arrives (dropped SSE frame, etc.).
	const interruptTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
	const partialReplayIdsRef = useRef(new Set<string>());
	const pendingUserMessagesRef = useRef(new Map<string, Msg>());

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

	/** Apply a single AgentEvent to the in-progress reply. */
	const processEvent = useCallback(
		(event: AgentEvent) => {
			const eventReplyId = 'reply_id' in event ? event.reply_id : undefined;
			setLastActivity((previous) =>
				!previous || Date.parse(event.created_at) > Date.parse(previous)
					? event.created_at
					: previous,
			);
			if (event.type === EventType.MODEL_CALL_START) {
				setRunStage('model');
				setActiveToolName(null);
			}
			if (
				event.type === EventType.TOOL_CALL_START ||
				event.type === EventType.TOOL_RESULT_START
			) {
				setRunStage('tool');
				setActiveToolName(event.tool_call_name);
			}
			if (event.type === EventType.REQUIRE_USER_CONFIRM) setRunStage('awaiting_confirmation');
			if (event.type === EventType.REQUIRE_EXTERNAL_EXECUTION) {
				setRunStage('awaiting_external_result');
			}
			if (event.type === EventType.REPLY_END) {
				setRunStage('finalizing');
				setActiveToolName(null);
			}

			// Custom events are service-layer notifications, not agent
			// reply content — route them to callbacks and skip appendEvent.
			if (event.type === EventType.CUSTOM) {
				const custom = event as CustomEvent;
				if (custom.name === 'external_execution_result_rejected') {
					const failure = custom.value as {
						request_id: string;
						reply_id: string;
						tool_call_ids: string[];
					};
					const pending = externalSubmissionsRef.current.get(failure.request_id);
					if (
						pending?.replyId === failure.reply_id &&
						failure.tool_call_ids.some((id) => pending.toolCallIds.has(id))
					) {
						externalSubmissionsRef.current.delete(failure.request_id);
						const error = new Error(t('chat.externalExecution.rejected'));
						pending.reject(error);
						toast.error(error.message);
					}
				} else if (custom.name === 'tts_error') {
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
					// (worker_session_id, reply_id).
					const e = custom.value as unknown as SubagentHitlEntry;
					setSubagentHitl((prev) => [
						...prev.filter((x) => hitlKey(x) !== hitlKey(e)),
						e,
					]);
				} else if (custom.name === 'subagent_user_confirm_result') {
					// The member resolved (or its run ended); clear the card.
					const v = custom.value as { worker_session_id: string; reply_id: string };
					setSubagentHitl((prev) => prev.filter((x) => hitlKey(x) !== hitlKey(v)));
				} else if (custom.name === 'chat_input_started') {
					const startedItem = startItem(custom.value as ChatInputStartedValue);
					if (startedItem) {
						for (const message of getQueueMessages([startedItem])) {
							if (message.role === 'user')
								pendingUserMessagesRef.current.set(message.id, message);
						}
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

			// A reconnect can start after the replay buffer has already evicted
			// REPLY_START. Keep a clearly marked assistant shell so subsequent
			// deltas are visible, without inventing the missing text or tool input.
			if (
				eventReplyId &&
				event.type !== EventType.REPLY_START &&
				!msgsRef.current.some((message) => message.id === eventReplyId)
			) {
				const partial = createPartialReply(event)!;
				msgsRef.current = [...msgsRef.current, partial];
				currentReplyRef.current = partial;
				partialReplayIdsRef.current.add(eventReplyId);
				setActiveReplyId(eventReplyId);
				setPhase('streaming');
			}
			if (event.type === EventType.TOOL_RESULT_END) {
				const result = event as ToolResultEndEvent;
				for (const [requestId, pending] of externalSubmissionsRef.current) {
					if (pending.replyId !== result.reply_id) continue;
					pending.toolCallIds.delete(result.tool_call_id);
					if (pending.toolCallIds.size === 0) {
						externalSubmissionsRef.current.delete(requestId);
						pending.resolve();
					}
				}
			} else if (event.type === EventType.REPLY_END) {
				for (const [requestId, pending] of externalSubmissionsRef.current) {
					if (pending.replyId !== event.reply_id) continue;
					externalSubmissionsRef.current.delete(requestId);
					pending.reject(new Error(t('chat.externalExecution.rejected')));
				}
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
				setRunStage('preparing');
				setActiveToolName(null);
			} else {
				// Realtime providers may start the assistant response before the
				// user's final transcription arrives. Route every identified event
				// to its own reply instead of assuming event streams never overlap.
				const reply = findReplyById(
					msgsRef.current,
					event.reply_id ?? currentReplyRef.current?.id,
				);
				if (reply) {
					appendMessageEvent(reply, event);
					// ``appendEvent`` mutates in place, which would leave
					// every Msg identical across renders and force the whole
					// list to re-render on each delta. Republish just the
					// reply that changed under a fresh identity, so the
					// memoised bubbles of the other messages can skip the
					// render. Anything holding a Msg reference across events
					// must re-read it from here — ``currentReplyRef`` below,
					// everything else looks the reply up by id.
					const updated = { ...reply, content: [...reply.content] };
					msgsRef.current = msgsRef.current.map((m) => (m.id === reply.id ? updated : m));
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
					setRunStage(null);
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

	// ── Lifecycle: fetch history + open SSE stream ──────────────────
	useEffect(() => {
		setLoadedKey(null);
		msgsRef.current = [];
		currentReplyRef.current = null;
		setMsgs([]);
		setError(null);
		clearInterruptTimer();
		setPhase('idle');
		setRunStage(null);
		setActiveToolName(null);
		setConnectionState('disconnected');
		setLastActivity(null);
		setStreamError(null);
		setActiveReplyId(null);
		setSubagentHitl([]);
		partialReplayIdsRef.current.clear();
		pendingUserMessagesRef.current.clear();
		audioManager?.disposeAll();

		if (!agentId || !sessionId) return;

		const controller = new AbortController();
		abortRef.current = controller;
		let cancelled = false;
		const currentKey = `${agentId}:${sessionId}`;
		const recoveryReplyIds = new Set<string>();
		const attemptedRepairs = new Set<string>();

		const applyHistory = async (markLoaded: boolean) => {
			const { messages, is_running, event_cursor } = await loadReplyCheckpoint(
				() =>
					sessionApi.messages(sessionId, agentId, undefined, {
						signal: controller.signal,
						silent: true,
					}),
				recoveryReplyIds,
				controller.signal,
			);
			if (cancelled) return null;
			// The history endpoint is the canonical snapshot. It repairs any
			// partial replay shell once the complete message is persisted.
			partialReplayIdsRef.current.clear();
			for (const message of messages) pendingUserMessagesRef.current.delete(message.id);
			msgsRef.current = mergeHistoryWithPendingUsers(
				messages,
				pendingUserMessagesRef.current,
			);
			const tail = messages[messages.length - 1];
			const pendingHitl = getPendingHitlCalls(messages)[0];
			// Every ref must point at the new canonical objects. A matching ID
			// on an old object is insufficient for append/publish by identity.
			currentReplyRef.current = findReplyById(
				msgsRef.current,
				pendingHitl?.replyId ?? currentReplyRef.current?.id,
			);
			if (is_running || pendingHitl) {
				setPhase('streaming');
				setRunStage(
					pendingHitl?.kind === 'external_execution'
						? 'awaiting_external_result'
						: pendingHitl
							? 'awaiting_confirmation'
							: 'preparing',
				);
				setActiveToolName(null);
				if (is_running && !pendingHitl && tail?.role === 'assistant') {
					setActiveReplyId(tail.id);
					currentReplyRef.current = tail;
				}
				if (pendingHitl) {
					currentReplyRef.current =
						messages.find((message) => message.id === pendingHitl.replyId) ?? null;
				}
			} else {
				setPhase('idle');
				setRunStage(null);
				setActiveToolName(null);
				setActiveReplyId(null);
				currentReplyRef.current = null;
			}
			setMsgs([...msgsRef.current]);
			if (markLoaded) setLoadedKey(currentKey);
			return event_cursor;
		};

		(async () => {
			let firstLoad = true;
			let delayMs = 500;
			while (!cancelled && !controller.signal.aborted) {
				const streamController = new AbortController();
				const abortStream = () => streamController.abort();
				controller.signal.addEventListener('abort', abortStream, { once: true });
				let repairingReply = false;
				try {
					setConnectionState(firstLoad ? 'connecting' : 'reconnecting');
					setRunStage((stage) => (stage === null ? 'reconnecting' : stage));
					const freshlyCreated = takeFreshlyCreated(sessionId);
					const eventCursor = freshlyCreated ? null : await applyHistory(firstLoad);
					recoveryReplyIds.clear();
					if (freshlyCreated && firstLoad) setLoadedKey(currentKey);
					if (cancelled) break;
					firstLoad = false;
					for await (const event of sessionApi.streamEvents(
						sessionId,
						agentId,
						streamController.signal,
						eventCursor,
						{
							onOpen: () => {
								if (
									cancelled ||
									controller.signal.aborted ||
									streamController.signal.aborted
								)
									return;
								setConnectionState('connected');
								setStreamError(null);
								delayMs = 500;
							},
						} satisfies StreamEventsObserver,
					)) {
						if (cancelled) break;
						processEvent(event);
						if (
							event.type === EventType.REPLY_END &&
							partialReplayIdsRef.current.has(event.reply_id) &&
							!attemptedRepairs.has(event.reply_id)
						) {
							// Stop consuming deltas before fetching the canonical
							// reply/checkpoint. Never merge a new stream into a
							// snapshot fetched concurrently with that stream.
							recoveryReplyIds.add(event.reply_id);
							attemptedRepairs.add(event.reply_id);
							repairingReply = true;
							streamController.abort();
							break;
						}
					}
					if (repairingReply) continue;
					if (!cancelled) throw new Error(t('chat.streamClosed'));
				} catch (e) {
					if (cancelled || controller.signal.aborted) break;
					if (repairingReply) continue;
					if (firstLoad) setLoadedKey(currentKey);
					setConnectionState('reconnecting');
					setRunStage((stage) => (stage === null ? 'reconnecting' : stage));
					setStreamError(e as Error);
					try {
						await waitForAbortableDelay(delayMs, controller.signal);
					} catch {
						break;
					}
					delayMs = Math.min(delayMs * 2, 8_000);
				} finally {
					streamController.abort();
					controller.signal.removeEventListener('abort', abortStream);
				}
			}
		})();

		return () => {
			cancelled = true;
			controller.abort();
			abortRef.current = null;
			clearInterruptTimer();
		};
	}, [agentId, sessionId, processEvent, audioManager, clearInterruptTimer, t]);

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

			setError(null);
			const userMsg = UserMsg({ name: 'user', content });
			const showInConversationImmediately = phase === 'idle' && pendingQueueCount === 0;
			if (showInConversationImmediately) {
				pendingUserMessagesRef.current.set(userMsg.id, userMsg);
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
				pendingUserMessagesRef.current.delete(userMsg.id);
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

			// Restore the ref so continuation events (no REPLY_START)
			// have a target.
			currentReplyRef.current = msgsRef.current.find((m) => m.id === replyId) ?? null;

			const event: UserConfirmResultEvent = {
				type: EventType.USER_CONFIRM_RESULT,
				id: crypto.randomUUID(),
				created_at: new Date().toISOString(),
				reply_id: replyId,
				confirm_results: [
					{ confirmed: confirm, tool_call: toolCall, rules: rules ?? null },
				],
			};

			try {
				if (
					optionsRef.current?.isRealtimeAudioActive?.() &&
					optionsRef.current.sendRealtimeUserConfirm
				) {
					await optionsRef.current.sendRealtimeUserConfirm(event);
				} else {
					await chatApi.trigger({
						agent_id: agentId,
						session_id: sessionId,
						input: event,
					});
				}
			} catch (e) {
				setError(e as Error);
				throw e;
			}
		},
		[agentId, sessionId],
	);

	const onExternalExecutionResult = useCallback(
		async (event: ExternalExecutionResultEvent) => {
			if (!agentId || !sessionId || activeSessionRef.current !== session) return;
			try {
				if (
					optionsRef.current?.isRealtimeAudioActive?.() &&
					optionsRef.current.sendRealtimeExternalExecutionResult
				) {
					// Sending a DataChannel frame does not acknowledge its result.
					// Register first so even an immediate SSE response is matched.
					const receipt = new Promise<void>((resolve, reject) => {
						externalSubmissionsRef.current.set(event.id, {
							replyId: event.reply_id,
							toolCallIds: new Set(
								event.execution_results.map((result) => result.id),
							),
							resolve,
							reject,
						});
					});
					await Promise.all([
						receipt,
						optionsRef.current.sendRealtimeExternalExecutionResult(event),
					]);
				} else {
					await chatApi.trigger({
						agent_id: agentId,
						session_id: sessionId,
						input: event,
					});
				}
			} catch (e) {
				if (activeSessionRef.current === session) setError(e as Error);
				throw e;
			} finally {
				externalSubmissionsRef.current.delete(event.id);
			}
		},
		[agentId, sessionId, session],
	);

	/** Return a client-executed tool result and resume the parked reply. */
	const onClientExternalToolResult = useCallback(
		async (toolCall: ToolCallBlock, result: ClientExternalToolResult, replyId: string) => {
			if (!agentId || !sessionId || activeSessionRef.current !== session) return;

			currentReplyRef.current = msgsRef.current.find((msg) => msg.id === replyId) ?? null;
			const createdAt = new Date().toISOString();
			const nativeResult =
				toolCall.name === ASK_USER_TOOL_NAME ? parseAskUserResult(result) : null;
			if (toolCall.name === ASK_USER_TOOL_NAME && !nativeResult) {
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
				state: 'success',
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

			await onExternalExecutionResult(event);
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
	 * The result is POSTed to the **leader** session (the
	 * ``(agentId, sessionId)`` this hook is bound to), NOT the worker.
	 * The backend resolves ``reply_id`` → worker session via the
	 * leader's pending hash and forwards the event to the worker's
	 * continuation. The client never addresses the worker directly —
	 * ``entry.worker_*`` ids are used only for local dedup / clearing.
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

			const event: UserConfirmResultEvent = {
				type: EventType.USER_CONFIRM_RESULT,
				id: crypto.randomUUID(),
				created_at: new Date().toISOString(),
				reply_id: entry.reply_id, // worker's reply_id; backend maps it
				confirm_results: [
					{ confirmed: confirm, tool_call: toolCall, rules: rules ?? null },
				],
			};

			try {
				// Post to the leader front door — backend routes to the
				// worker session (§3.6). Do NOT address the worker here.
				await chatApi.trigger({
					agent_id: agentId,
					session_id: sessionId,
					input: event,
				});
			} catch (e) {
				setError(e as Error);
				// Rethrow so the card can re-enable itself and be retried.
				throw e;
			}

			// Drop only the call just answered — an entry can carry several
			// pending tool calls, and clearing the whole entry would take the
			// unanswered siblings' cards down with it. The backend's clear
			// event removes whatever is left.
			setSubagentHitl((prev) =>
				prev.flatMap((x) => {
					if (hitlKey(x) !== hitlKey(entry)) return [x];
					const remaining = (x.event.tool_calls ?? []).filter(
						(tc) => tc.id !== toolCall.id,
					);
					return remaining.length > 0
						? [{ ...x, event: { ...x.event, tool_calls: remaining } }]
						: [];
				}),
			);
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
		runStage: ownsConversation ? runStage : null,
		activeToolName: ownsConversation ? activeToolName : null,
		connectionState: ownsConversation
			? connectionState
			: ('disconnected' as StreamConnectionState),
		lastActivity: ownsConversation ? lastActivity : null,
		streamError: ownsConversation ? streamError : null,
		error: ownsConversation ? error : null,
		activeReplyId: ownsConversation && phase === 'streaming' ? activeReplyId : null,
		steerQueued,
		send,
		onUserConfirm,
		onClientExternalToolResult,
		onExternalExecutionResult,
		onSubagentConfirm,
		subagentHitl: ownsConversation ? subagentHitl : [],
		abort,
		interrupt,
	};
}
