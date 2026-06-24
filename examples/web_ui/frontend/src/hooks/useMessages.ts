import { EventType } from '@agentscope-ai/agentscope/event';
import type {
	AgentEvent,
	CustomEvent,
	DataBlockStartEvent,
	DataBlockDeltaEvent,
	DataBlockEndEvent,
	ReplyStartEvent,
	UserConfirmResultEvent,
} from '@agentscope-ai/agentscope/event';
import { appendEvent, AssistantMsg, UserMsg } from '@agentscope-ai/agentscope/message';
import type { Msg, ContentBlock } from '@agentscope-ai/agentscope/message';
import type { ToolCallBlock } from '@agentscope-ai/agentscope/message';
import { useState, useCallback, useRef, useEffect } from 'react';

import { sessionApi } from '@/api';
import { chatApi } from '@/api';
import { useAudioManager } from '@/context/AudioContext';

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
 * history. User input and human-in-the-loop confirmations are sent
 * via ``POST /chat/`` (fire-and-forget); the resulting events arrive
 * through the already-open SSE connection.
 *
 * ``streaming`` is driven by event content, not HTTP lifecycle:
 * ``true`` after receiving ``ReplyStartEvent``, ``false`` after
 * ``ReplyEndEvent``.
 *
 * @param agentId - The agent whose session to subscribe. ``null`` to
 *   skip.
 * @param sessionId - The session to subscribe. ``null`` to skip.
 * @returns Object with ``msgs``, ``loading``, ``streaming``, ``error``,
 *   ``send``, ``onUserConfirm``, and ``abort``.
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
		 * When true, SSE events will not route audio to the manager.
		 * In voice/realtime mode, audio is delivered via WebSocket and
		 * should be routed from there instead of SSE replay.
		 */
		voiceModeRef?: React.RefObject<boolean>;
	},
) {
	const [msgs, setMsgs] = useState<Msg[]>([]);
	const [loading, setLoading] = useState(false);
	const [streaming, setStreaming] = useState(false);
	const [error, setError] = useState<Error | null>(null);
	// Pending subagent HITL cards projected onto this (leader) session.
	const [subagentHitl, setSubagentHitl] = useState<SubagentHitlEntry[]>([]);

	const msgsRef = useRef<Msg[]>([]);
	const currentReplyRef = useRef<Msg | null>(null);
	const abortRef = useRef<AbortController | null>(null);
	const rafRef = useRef<number | null>(null);
	const seenEventIds = useRef<Set<string>>(new Set());

	const audioManager = useAudioManager();

	/** Reply IDs already present in the loaded history. Events arriving
	 *  from the SSE replay log that target these replies are skipped
	 *  entirely — they would otherwise corrupt the historical message
	 *  (double-appending text) or trigger unwanted audio autoplay. */
	const skippedReplyIds = useRef<Set<string>>(new Set());

	/** Tracks whether the initial SSE replay has completed. Audio
	 *  routing is suppressed during replay to prevent auto-play of
	 *  stale events from previous realtime sessions. */
	const sseReplayDone = useRef(false);

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

	/**
	 * Apply a single AgentEvent to the in-progress reply.
	 *
	 * Exported so that ``useRealtimeSession`` can feed WebSocket events
	 * through the same pipeline (audio manager, message list, streaming
	 * state) without duplicating logic.
	 */
	const processEvent = useCallback(
		(event: AgentEvent, routeAudio?: boolean) => {
			// routeAudio defaults: true in text mode, false in voice mode
			// (SSE replay in voice mode should never trigger audio playback)
			const shouldRouteAudio = routeAudio ?? !optionsRef.current?.voiceModeRef?.current;

			if (event.id && seenEventIds.current.has(event.id)) return;
			if (event.id) {
				seenEventIds.current.add(event.id);
				if (seenEventIds.current.size > 2000) {
					const entries = [...seenEventIds.current];
					seenEventIds.current = new Set(entries.slice(-1000));
				}
			}

			const t = event.type as string;

			// Show the user's speech as a chat message once transcribed.
			// The transcription often arrives *after* the model has already
			// started replying, so we insert it right before the current
			// in-progress reply to preserve chronological order.
			if (t === 'USER_INPUT_TRANSCRIPTION') {
				const transcript = (event as unknown as { transcript: string }).transcript;
				if (transcript) {
					const userMsg = UserMsg({
						name: 'user',
						content: [{ id: crypto.randomUUID(), type: 'text', text: transcript }],
					});
					const cur = currentReplyRef.current;
					if (cur) {
						const idx = msgsRef.current.findIndex((m) => m.id === cur.id);
						if (idx >= 0) {
							const copy = [...msgsRef.current];
							copy.splice(idx, 0, userMsg);
							msgsRef.current = copy;
						} else {
							msgsRef.current = [...msgsRef.current, userMsg];
						}
					} else {
						msgsRef.current = [...msgsRef.current, userMsg];
					}
					scheduleUpdate();
				}
				return;
			}

			// Stop audio playback when the user starts speaking (barge-in).
			if (t === 'USER_INPUT_AUDIO_START') {
				audioManager?.stopAllPlayback();
				return;
			}

			if (t === 'USER_INPUT_AUDIO_END') {
				return;
			}
			// Custom events are service-layer notifications, not agent
			// reply content — route them to callbacks and skip appendEvent.
			if (event.type === EventType.CUSTOM) {
				const custom = event as CustomEvent;
				if (custom.name === 'team_updated') {
					optionsRef.current?.onTeamUpdated?.();
				} else if (custom.name === 'state_updated' && custom.value) {
					optionsRef.current?.onStateUpdated?.(custom.value as Record<string, unknown>);
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
				}
				return;
			}
			if (event.type === EventType.REPLY_START) {
				audioManager?.stopAllPlayback();
				const e = event as ReplyStartEvent;
				// If this reply is already tracked for skipping (preloaded from
				// history), discard the replay event immediately.
				if (skippedReplyIds.current.has(e.reply_id)) {
					return;
				}
				// If a message with this reply_id already exists (e.g. created
				// moments ago via WebSocket while this SSE event was in transit),
				// discard the duplicate WITHOUT adding to skippedReplyIds — the
				// reply is live, not historical.
				const existing = msgsRef.current.find((m) => m.id === e.reply_id);
				if (existing) {
					return;
				}
				const msg = AssistantMsg({
					id: e.reply_id,
					name: e.name,
					content: [],
					created_at: e.created_at,
				});
				msgsRef.current = [...msgsRef.current, msg];
				currentReplyRef.current = msg;
				setStreaming(true);
			} else if (event.type === EventType.REPLY_END) {
				if (currentReplyRef.current) {
					appendEvent(currentReplyRef.current, event);
				}
				setStreaming(false);
				currentReplyRef.current = null;
			} else if (currentReplyRef.current) {
				appendEvent(currentReplyRef.current, event);
			} else if ('reply_id' in event) {
				const replyId = (event as { reply_id: string }).reply_id;
				// Skip events targeting a historical reply to avoid
				// corrupting already-complete messages and triggering
				// unwanted audio playback from SSE replay.
				if (skippedReplyIds.current.has(replyId)) {
					return;
				}
				// Late-arriving events (e.g. tool results from realtime
				// sessions that arrive after REPLY_END). Find the target
				// message by reply_id so the result is appended correctly.
				const msg = msgsRef.current.find((m) => m.id === replyId);
				if (msg) {
					appendEvent(msg, event);
				}
			}

			// Route streaming audio DataBlocks to the audio manager. They still
			// flow through `appendEvent` above (which builds up `source.data`
			// in the Msg), but MessageBubble reads playback state from the
			// manager so it can show progress and autoplay on completion.
			// Guard: skip when shouldRouteAudio is false. For SSE events
			// (routeAudio undefined), additionally require sseReplayDone to
			// suppress stale replay audio. WebSocket events pass routeAudio=true
			// and bypass the sseReplayDone gate.
			if (
				shouldRouteAudio &&
				(routeAudio || sseReplayDone.current) &&
				audioManager &&
				(routeAudio ||
					!(
						'reply_id' in event &&
						skippedReplyIds.current.has((event as { reply_id: string }).reply_id)
					))
			) {
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
					audioManager.end(e.block_id);
				}
			}

			// Skip full message-list re-render for audio-only delta events.
			// Audio UI updates independently via useSyncExternalStore in
			// AudioInlineControl, so re-rendering all MessageBubbles is wasteful.
			if (event.type === EventType.DATA_BLOCK_DELTA) {
				const e = event as DataBlockDeltaEvent;
				if (e.media_type.startsWith('audio/')) return;
			}

			scheduleUpdate();
		},
		[scheduleUpdate, audioManager],
	);

	// ── Lifecycle: fetch history + open SSE stream ──────────────────
	useEffect(() => {
		msgsRef.current = [];
		currentReplyRef.current = null;
		skippedReplyIds.current.clear();
		sseReplayDone.current = false;
		// Only reset seenEventIds if this is a truly new session or we need a clean state
		// For reconnecting to existing sessions, we want to avoid re-processing events
		// Clear old entries occasionally to prevent memory leaks
		if (seenEventIds.current.size > 2000) {
			const entries = [...seenEventIds.current];
			seenEventIds.current = new Set(entries.slice(-1000));
		}
		setMsgs([]);
		setError(null);
		setStreaming(false);
		setSubagentHitl([]);
		audioManager?.disposeAll();

		if (!agentId || !sessionId) return;

		const controller = new AbortController();
		abortRef.current = controller;
		let cancelled = false;

		(async () => {
			// 1. Fetch persisted history
			setLoading(true);
			try {
				const { messages } = await sessionApi.messages(sessionId, agentId);
				if (cancelled) return;
				msgsRef.current = messages;
				// Pre-populate skippedReplyIds with all loaded message IDs.
				// This ensures that ANY SSE replay event targeting a historical
				// message is immediately blocked from triggering audio playback,
				// even if the replay arrives before the WebSocket trim completes.
				for (const m of messages) {
					if (m.id) skippedReplyIds.current.add(m.id);
				}
				scheduleUpdate();
			} catch (e) {
				if (!cancelled) setError(e as Error);
				return;
			} finally {
				if (!cancelled) setLoading(false);
			}

			// 2. Open SSE long connection for live events
			try {
				for await (const event of sessionApi.streamEvents(
					sessionId,
					agentId,
					controller.signal,
				)) {
					if (cancelled) break;
					// Detect the server-sent replay-done sentinel
					if ((event as { type: string }).type === 'REPLAY_DONE') {
						sseReplayDone.current = true;
						continue;
					}
					processEvent(event);
				}
			} catch (e) {
				if ((e as Error).name !== 'AbortError' && !cancelled) {
					setError(e as Error);
				}
			} finally {
				sseReplayDone.current = true;
			}
		})();

		return () => {
			cancelled = true;
			controller.abort();
			abortRef.current = null;
		};
	}, [agentId, sessionId, scheduleUpdate, processEvent, audioManager]);

	/**
	 * Send a user message. Appends the message to the local list
	 * optimistically, then fires a ``POST /chat/`` trigger. Events
	 * arrive via the already-open SSE connection.
	 *
	 * @param content - The message content blocks.
	 */
	const send = useCallback(
		async (content: ContentBlock[]) => {
			if (!agentId || !sessionId) return;

			const userMsg = UserMsg({ name: 'user', content });
			msgsRef.current = [...msgsRef.current, userMsg];
			scheduleUpdate();

			try {
				await chatApi.trigger({
					agent_id: agentId,
					session_id: sessionId,
					input: userMsg,
				});
			} catch (e) {
				setError(e as Error);
			}
		},
		[agentId, sessionId, scheduleUpdate],
	);

	/**
	 * Confirm or deny a tool call (human-in-the-loop). Fires a
	 * ``POST /chat/`` with a ``UserConfirmResultEvent``; events
	 * arrive via SSE.
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
				await chatApi.trigger({
					agent_id: agentId,
					session_id: sessionId,
					input: event,
				});
			} catch (e) {
				setError(e as Error);
			}
		},
		[agentId, sessionId],
	);

	/** Abort the current SSE connection. */
	const abort = useCallback(() => {
		abortRef.current?.abort();
	}, []);

	/** Append a message to the local list without triggering any API call.
	 *  Used by voice mode to display user text input immediately. */
	const appendLocalMsg = useCallback(
		(msg: Msg) => {
			msgsRef.current = [...msgsRef.current, msg];
			scheduleUpdate();
		},
		[scheduleUpdate],
	);

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

			// Optimistically clear; the backend's clear event re-confirms.
			setSubagentHitl((prev) => prev.filter((x) => hitlKey(x) !== hitlKey(entry)));

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
			}
		},
		[agentId, sessionId],
	);

	return {
		msgs,
		loading,
		streaming,
		error,
		send,
		onUserConfirm,
		onSubagentConfirm,
		subagentHitl,
		abort,
		processEvent,
		appendLocalMsg,
	};
}
