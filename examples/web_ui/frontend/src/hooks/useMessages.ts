import { EventType, ReplyFinishedReason } from '@agentscope-ai/agentscope/event';
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
import { ApiError, type ApiScope } from '@/api/client';
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
import { appendMessageEvent, audioDeltaUrl } from '@/lib/message-events';
import { getPendingHitlCalls } from '@/lib/pendingToolCalls';
import type { VoicePlayback } from '@/lib/pipelineVoiceSession';

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

/** Safety fallback: force phase back to idle if REPLY_END is not seen. */
const INTERRUPT_TIMEOUT_MS = 10_000;

const EXTERNAL_REQUEST_ID = '_agentscope_external_request_id';

type ToolSubmissionIssue = 'superseded' | 'unconfirmed';

/** The tool call has finished, but this submission has no success receipt. */
export class UnacknowledgedToolSubmission extends Error {
	constructor(reason: ToolSubmissionIssue) {
		super(reason);
	}
}

interface ExternalSubmission {
	replyId: string;
	toolCallIds: Set<string>;
	ambiguousCalls: Set<string>;
	issue?: ToolSubmissionIssue;
	allowRetry: () => void;
	finish: (issue?: ToolSubmissionIssue) => void;
	reject: (error: Error) => void;
}

function settleToolResult(
	submission: ExternalSubmission,
	requestId: string,
	callId: string,
	metadata?: Record<string, unknown>,
): boolean {
	const owner = metadata?.[EXTERNAL_REQUEST_ID];
	if (owner !== undefined && typeof owner !== 'string') return false;
	if (!submission.toolCallIds.delete(callId)) return false;
	if (owner === undefined && submission.ambiguousCalls.has(callId)) {
		submission.issue ??= 'unconfirmed';
	} else if (owner !== undefined && owner !== requestId) {
		submission.issue = 'superseded';
	}
	if (submission.toolCallIds.size) return false;
	submission.finish(submission.issue);
	return true;
}

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
		/** Route playback, including deferred ownership or a pending block end. */
		onVoiceEvent?: (event: AgentEvent) => VoicePlayback;
		onStreamClosed?: () => void;
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
	const pendingChatPosts = useRef(new Map<string, (queueId: string) => void>());
	const externalSubmissionsRef = useRef(new Map<string, ExternalSubmission>());
	const activeSessionRef = useRef<typeof session | null>(null);
	useLayoutEffect(() => {
		activeSessionRef.current = session;
		const chatPosts = pendingChatPosts.current;
		const externalSubmissions = externalSubmissionsRef.current;
		return () => {
			activeSessionRef.current = null;
			chatPosts.clear();
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
	const [streamConnected, setStreamConnected] = useState(false);
	const [startingInputId, setStartingInputId] = useState<string | null>(null);
	// Only a running reply accepts steering. Parked replies retain their
	// streaming phase and currentReplyRef for confirmation and Stop.
	const [activeReplyId, setActiveReplyId] = useState<string | null>(null);
	const [error, setError] = useState<Error | null>(null);
	// Pending subagent HITL cards projected onto this (leader) session.
	const [subagentHitl, setSubagentHitl] = useState<SubagentHitlEntry[]>([]);
	const msgsRef = useRef<Msg[]>([]);
	const currentReplyRef = useRef<Msg | null>(null);
	const startingQueueRef = useRef<string | null>(null);
	const replyQueueRef = useRef<string | null>(null);
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

	const removeProvisional = useCallback(
		(messages: Set<Msg>) => {
			if (!messages.size) return;
			msgsRef.current = msgsRef.current.filter((msg) => !messages.has(msg));
			scheduleUpdate();
		},
		[scheduleUpdate],
	);
	const {
		items: queuedItems,
		visibleCount: queuedCount,
		pendingCount: pendingQueueCount,
		hasPendingInput: hasQueuedInput,
		reorderDisabled: queueReorderDisabled,
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
	} = useChatQueue(agentId, sessionId, setError, removeProvisional);

	const removeUnstartedInput = useCallback(
		(value: ChatInputTerminalValue) => {
			removeProvisional(retireItem(value, 'removed'));
		},
		[retireItem, removeProvisional],
	);

	/** Apply a single AgentEvent to the in-progress reply. */
	const processEvent = useCallback(
		(event: AgentEvent) => {
			const voicePlayback = optionsRef.current?.onVoiceEvent?.(event) ?? false;
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
					const value = custom.value as ChatInputStartedValue;
					startingQueueRef.current = value.queue_item_id ?? null;
					setStartingInputId(startingQueueRef.current);
					if (value.client_request_id && value.queue_item_id) {
						pendingChatPosts.current.get(value.client_request_id)?.(
							value.queue_item_id,
						);
					}
					const { item: startedItem, provisional } = startItem(value);
					if (startedItem) {
						msgsRef.current = msgsRef.current.filter((msg) => !provisional.has(msg));
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
				} else if (custom.name === 'chat_input_removed') {
					const value = custom.value as ChatInputTerminalValue;
					if (value.queue_item_id) removeUnstartedInput(value);
				} else if (
					custom.name === 'chat_input_failed' ||
					custom.name === 'chat_input_cancelled'
				) {
					const value = custom.value as ChatInputTerminalValue;
					setStartingInputId((id) => (id === value.queue_item_id ? null : id));
					retireItem(value, 'finished');
					const queueId = value.queue_item_id;
					const wasStarting = !!queueId && queueId === startingQueueRef.current;
					if (wasStarting) startingQueueRef.current = null;
					if (
						(queueId && queueId === replyQueueRef.current) ||
						(wasStarting && !currentReplyRef.current && !replyQueueRef.current)
					) {
						replyQueueRef.current = null;
						clearInterruptTimer();
						currentReplyRef.current = null;
						setPhase('idle');
						setActiveReplyId(null);
					}
					if (custom.name === 'chat_input_failed') {
						const failure = new Error(value.message ?? t('chatQueue.processFailed'));
						setError(failure);
						toast.error(failure.message);
					} else {
						toast.info(value.message ?? t('chatQueue.processingCancelled'));
					}
				} else if (custom.name === 'chat_input_injected') {
					const value = custom.value as ChatInputTerminalValue;
					retireItem(value, 'finished');
					toast.success(t('chatQueue.injected'));
				} else if (custom.name === 'chat_input_steer_failed') {
					const value = custom.value as ChatInputTerminalValue;
					toast.error(value.message ?? t('chatQueue.steerFailed'));
				}
				return;
			}
			if (event.type === EventType.TOOL_RESULT_END) {
				const result = event as ToolResultEndEvent;
				for (const [id, request] of externalSubmissionsRef.current) {
					if (
						request.replyId === result.reply_id &&
						settleToolResult(request, id, result.tool_call_id, result.metadata)
					) {
						externalSubmissionsRef.current.delete(id);
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
				if (e.role === 'assistant') {
					if (startingQueueRef.current || currentReplyRef.current?.id !== e.reply_id) {
						replyQueueRef.current = startingQueueRef.current;
					}
					startingQueueRef.current = null;
					setStartingInputId(null);
				}
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
					audioManager?.stopAllPlayback(true);
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
					replyQueueRef.current = null;
				}
			}

			// Route streaming audio DataBlocks to the audio manager. They still
			// flow through `appendEvent` above (which builds up `source.data`
			// in the Msg), but MessageBubble reads playback state from the
			// manager so it can show progress and offer replay on completion.
			// Voice sessions own playback, but still need this stream state.
			if (audioManager) {
				if (event.type === EventType.DATA_BLOCK_START) {
					const e = event as DataBlockStartEvent;
					if (e.media_type.startsWith('audio/')) {
						const autoplay =
							typeof voicePlayback === 'object' && 'autoplay' in voicePlayback
								? voicePlayback.autoplay
								: !voicePlayback;
						audioManager.start(e.block_id, e.media_type, autoplay, e.reply_id);
					}
				} else if (event.type === EventType.DATA_BLOCK_DELTA) {
					const e = event as DataBlockDeltaEvent;
					if (e.media_type.startsWith('audio/')) {
						audioManager.append(e.block_id, e.data ? e : null, audioDeltaUrl(e));
					}
				} else if (event.type === EventType.DATA_BLOCK_END) {
					const e = event as DataBlockEndEvent;
					// `end` is a no-op when the block isn't being tracked, so
					// we can call it unconditionally.
					void audioManager.end(
						e.block_id,
						typeof voicePlayback === 'object' && 'then' in voicePlayback
							? voicePlayback
							: undefined,
					);
				} else if (
					event.type === EventType.REPLY_END &&
					event.finished_reason !== ReplyFinishedReason.COMPLETED
				) {
					audioManager.interruptReply(event.reply_id);
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
			retireItem,
			removeUnstartedInput,
			t,
			agentId,
			sessionId,
		],
	);

	// ── Lifecycle: fetch history + open SSE stream ──────────────────
	useEffect(() => {
		setLoadedKey(null);
		setStreamConnected(false);
		setStartingInputId(null);
		msgsRef.current = [];
		currentReplyRef.current = null;
		startingQueueRef.current = null;
		replyQueueRef.current = null;
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
		let retryTimer: ReturnType<typeof setTimeout> | undefined;
		let retryDelay = 1000;
		let oldestVisible: string | null | undefined;
		let savedHistory: Msg[] = [];
		let savedCursor: string | null = null;

		const connect = async (reconnecting: boolean) => {
			if (controller.signal.aborted) return;
			let connected = false;
			try {
				let eventCursor: string | null = null;
				const requests = new Map(reconnecting ? externalSubmissionsRef.current : []);
				if (reconnecting || !takeFreshlyCreated(sessionId)) {
					const options = {
						signal: controller.signal,
						silent: reconnecting,
						timeoutMs: 15_000,
					};
					const latest = await sessionApi.messages(
						sessionId,
						agentId,
						undefined,
						options,
					);
					if (controller.signal.aborted) return;
					eventCursor = latest.event_cursor;
					const { is_running } = latest;
					const pages = [latest.messages];
					let page = latest;
					while (
						oldestVisible !== undefined &&
						page.has_more &&
						page.messages.length &&
						!page.messages.some((msg) => msg.id === oldestVisible)
					) {
						// A shared checkpoint and unchanged message identities let us
						// reuse the persisted prefix without retaining live deltas.
						if (eventCursor !== null && eventCursor === savedCursor) {
							const overlap = savedHistory.findIndex(
								(msg) => msg.id === page.messages[0].id,
							);
							if (overlap >= 0) {
								const prefix = savedHistory.slice(0, overlap);
								const visible = new Set(msgsRef.current);
								if (prefix.every((msg) => visible.has(msg))) {
									pages.push(prefix);
									break;
								}
							}
						}
						page = await sessionApi.messages(
							sessionId,
							agentId,
							{
								before: page.messages[0].id,
								limit: 200,
							},
							options,
						);
						if (controller.signal.aborted) return;
						const floor = page.messages.findIndex((msg) => msg.id === oldestVisible);
						pages.push(floor < 0 ? page.messages : page.messages.slice(floor));
					}
					const messages = pages.reverse().flat();
					savedHistory = messages;
					savedCursor = eventCursor;
					const tail = messages.at(-1);
					if (reconnecting) {
						if (tail && !msgsRef.current.some((msg) => msg.id === tail.id)) {
							audioManager?.stopAllPlayback();
						}
						audioManager?.restoreCheckpoint(messages);
						setSubagentHitl([]);
					}
					const restored = reconnecting
						? restoreConversation(messages, controller.signal)
						: messages;
					if (controller.signal.aborted) return;
					oldestVisible = messages[0]?.id ?? null;
					for (const [id, request] of requests) {
						if (externalSubmissionsRef.current.get(id) !== request) continue;
						const reply = messages.find((msg) => msg.id === request.replyId);
						for (const block of reply?.content ?? []) {
							if (
								block.type === 'tool_result' &&
								block.state !== 'running' &&
								settleToolResult(request, id, block.id, block.metadata)
							) {
								externalSubmissionsRef.current.delete(id);
								requests.delete(id);
								break;
							}
						}
					}
					msgsRef.current = restored;
					startingQueueRef.current = null;
					replyQueueRef.current = null;
					setStartingInputId(null);
					clearInterruptTimer();
					const parked = getPendingHitlCalls(messages)[0];
					const active =
						is_running && !parked && tail?.role === 'assistant' && !tail.finished_reason
							? tail
							: null;
					currentReplyRef.current = parked
						? (messages.find((message) => message.id === parked.replyId) ?? null)
						: active;
					setActiveReplyId(active?.id ?? null);
					setPhase(is_running || parked ? 'streaming' : 'idle');
					setMsgs([...msgsRef.current]);
				} else oldestVisible = null;
				setLoadedKey(`${agentId}:${sessionId}`);
				const finishReplay = () => {
					if (controller.signal.aborted) return;
					for (const [id, request] of requests) {
						if (externalSubmissionsRef.current.get(id) !== request) continue;
						request.allowRetry();
					}
					requests.clear();
				};
				for await (const event of sessionApi.streamEvents(
					sessionId,
					agentId,
					controller.signal,
					eventCursor,
					() => {
						if (controller.signal.aborted) return;
						connected = true;
						setStreamConnected(true);
						if (reconnecting) setError(null);
					},
					{
						silent: reconnecting,
						onReplayComplete: requests.size ? finishReplay : undefined,
					},
				)) {
					if (controller.signal.aborted) return;
					retryDelay = 1000;
					processEvent(event);
				}
			} catch (e) {
				if (controller.signal.aborted) return;
				setLoadedKey(`${agentId}:${sessionId}`);
				setError(e as Error);
				if (e instanceof ApiError && [401, 403, 404].includes(e.status)) return;
			} finally {
				if (!controller.signal.aborted && connected) {
					setStreamConnected(false);
					optionsRef.current?.onStreamClosed?.();
				}
			}
			if (controller.signal.aborted) return;
			retryTimer = setTimeout(() => void connect(true), retryDelay);
			retryDelay = Math.min(retryDelay * 2, 15_000);
		};
		void connect(false);

		return () => {
			clearTimeout(retryTimer);
			controller.abort();
			abortRef.current = null;
			clearInterruptTimer();
		};
	}, [
		agentId,
		sessionId,
		processEvent,
		restoreConversation,
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
		async (
			content: ContentBlock[],
			observer?: {
				scope: ApiScope;
				submissionId?: string;
				signal?: AbortSignal;
				isCurrent?: () => boolean;
				keepPendingOnPostError?: () => boolean;
				submitted: (
					id: string,
					clearPending: (beforeClaim: boolean) => void,
					acceptedQueue: (id: string) => void,
				) => void;
				accepted: (id: string | null) => void;
			},
			mode: 'chat' | 'pipeline_voice' = 'chat',
		) => {
			if (!agentId || !sessionId || activeSessionRef.current !== session) return;

			const userMsg = UserMsg({ name: 'user', content });
			const confirmQueue = (id: string) => {
				if (activeSessionRef.current !== session) return;
				const outcome = acceptOptimistic(userMsg.id, id);
				if (outcome === 'removed') {
					removeUnstartedInput({ queue_item_id: id, message_ids: [userMsg.id] });
				} else if (typeof outcome === 'object') {
					msgsRef.current = msgsRef.current.map((msg) =>
						msg === userMsg ? outcome : msg,
					);
					scheduleUpdate();
				}
			};
			observer?.submitted(
				userMsg.id,
				(beforeClaim) => {
					if (activeSessionRef.current !== session) return;
					const value = { message_ids: [userMsg.id] };
					if (beforeClaim) removeUnstartedInput(value);
					else retireItem(value, 'finished');
				},
				confirmQueue,
			);
			const showInConversationImmediately = phase === 'idle' && pendingQueueCount === 0;
			if (showInConversationImmediately) {
				msgsRef.current = [...msgsRef.current, userMsg];
				scheduleUpdate();
			}
			const optimisticId = addOptimistic(userMsg, showInConversationImmediately);
			const requestId = observer ? undefined : crypto.randomUUID();
			const startReceipt = requestId
				? new Promise<string>((resolve) => pendingChatPosts.current.set(requestId, resolve))
				: undefined;
			try {
				const post = chatApi
					.trigger(
						{
							agent_id: agentId,
							session_id: sessionId,
							input: userMsg,
							mode,
							submission_id: observer?.submissionId,
							client_request_id: requestId,
						},
						{ ...observer?.scope, signal: observer?.signal, silent: true },
					)
					.then((response) => response.queue_item_id);
				// Either channel can confirm this exact submission first.
				const queueId = await (startReceipt ? Promise.race([post, startReceipt]) : post);
				if (observer?.signal?.aborted || (observer?.isCurrent && !observer.isCurrent()))
					return;
				if (queueId) confirmQueue(queueId);
				observer?.accepted(queueId);
			} catch (e) {
				// Voice cancellation tracks its own submission receipt.
				if (
					observer?.keepPendingOnPostError?.() ||
					(observer?.isCurrent && !observer.isCurrent())
				)
					throw e;
				if (activeSessionRef.current !== session) {
					// Voice cancellation outlives the viewport and still needs the outcome.
					if (observer) throw e;
					return;
				}
				// The server never accepted this input. Remove the
				// optimistic queue row and restore the caller's draft.
				rollbackOptimistic(optimisticId, userMsg.id);
				if (showInConversationImmediately) {
					msgsRef.current = msgsRef.current.filter((msg) => msg !== userMsg);
					scheduleUpdate();
				}
				setError(e as Error);
				if (!observer && e instanceof ApiError) toast.error(e.message);
				throw e;
			} finally {
				if (requestId) pendingChatPosts.current.delete(requestId);
			}
		},
		[
			agentId,
			sessionId,
			phase,
			pendingQueueCount,
			retireItem,
			removeUnstartedInput,
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
		async (event: ExternalExecutionResultEvent, onUnconfirmed?: (error: Error) => void) => {
			if (!agentId || !sessionId || activeSessionRef.current !== session) return;
			try {
				if (
					optionsRef.current?.isRealtimeAudioActive?.() &&
					optionsRef.current.sendRealtimeExternalExecutionResult
				) {
					if (!streamConnected) throw new Error(t('chat.externalExecution.reconnecting'));
					// Sending a DataChannel frame does not acknowledge its result.
					// Register first so even an immediate SSE response is matched.
					const receipt = new Promise<void>((resolve, reject) => {
						let notice: Error | undefined;
						const toolCallIds = new Set(
							event.execution_results.map((result) => result.id),
						);
						const ambiguousCalls = new Set<string>();
						// An untagged legacy result stays ambiguous even after one attempt settles.
						for (const previous of externalSubmissionsRef.current.values()) {
							if (previous.replyId !== event.reply_id) continue;
							for (const callId of toolCallIds) {
								if (previous.toolCallIds.has(callId)) {
									previous.ambiguousCalls.add(callId);
									ambiguousCalls.add(callId);
								}
							}
						}
						externalSubmissionsRef.current.set(event.id, {
							replyId: event.reply_id,
							toolCallIds,
							ambiguousCalls,
							allowRetry: () => {
								if (notice) return;
								notice = new Error(t('chat.externalExecution.unconfirmed'));
								setError(notice);
								onUnconfirmed?.(notice);
							},
							finish: (issue) => {
								if (notice)
									setError((current) => (current === notice ? null : current));
								if (issue === 'unconfirmed') {
									toast.info(t('chat.externalExecution.unknownAnswer'), {
										id: JSON.stringify([
											'unknown-answer',
											agentId,
											sessionId,
											event.reply_id,
										]),
									});
								}
								if (issue) reject(new UnacknowledgedToolSubmission(issue));
								else resolve();
							},
							reject,
						});
					});
					await Promise.all([
						receipt,
						optionsRef.current.sendRealtimeExternalExecutionResult({
							...event,
							metadata: { ...event.metadata, [EXTERNAL_REQUEST_ID]: event.id },
						}),
					]);
				} else {
					await chatApi.trigger({
						agent_id: agentId,
						session_id: sessionId,
						input: event,
					});
				}
			} catch (e) {
				if (
					activeSessionRef.current === session &&
					!(e instanceof UnacknowledgedToolSubmission)
				)
					setError(e as Error);
				throw e;
			} finally {
				if (activeSessionRef.current === session)
					externalSubmissionsRef.current.delete(event.id);
			}
		},
		[agentId, sessionId, session, streamConnected, t],
	);

	/** Return a client-executed tool result and resume the parked reply. */
	const onClientExternalToolResult = useCallback(
		async (
			toolCall: ToolCallBlock,
			result: ClientExternalToolResult,
			replyId: string,
			onUnconfirmed?: (error: Error) => void,
		) => {
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

			await onExternalExecutionResult(event, onUnconfirmed);
		},
		[agentId, sessionId, session, onExternalExecutionResult],
	);

	/** Abort the current SSE connection. */
	const abort = useCallback(() => {
		abortRef.current?.abort();
		setStreamConnected(false);
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
		streamConnected: ownsConversation && streamConnected,
		queuedItems: ownsConversation ? queuedItems : [],
		queuedCount: ownsConversation ? queuedCount : 0,
		hasPendingInput: hasQueuedInput || startingInputId !== null,
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
		onExternalExecutionResult,
		onSubagentConfirm,
		subagentHitl: ownsConversation ? subagentHitl : [],
		abort,
		interrupt,
	};
}
