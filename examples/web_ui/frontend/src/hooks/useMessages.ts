import { EventType } from '@agentscope-ai/agentscope/event';
import type {
	AgentEvent,
	ReplyStartEvent,
	UserConfirmResultEvent,
} from '@agentscope-ai/agentscope/event';
import { appendEvent, AssistantMsg, UserMsg } from '@agentscope-ai/agentscope/message';
import type { Msg, ContentBlock } from '@agentscope-ai/agentscope/message';
import type { ToolCallBlock } from '@agentscope-ai/agentscope/message';
import { useState, useCallback, useRef, useEffect } from 'react';

import { sessionApi } from '@/api';
import { chatApi } from '@/api';
import type { ChatRequest } from '@/api/types';

export function useMessages(agentId: string | null, sessionId: string | null) {
	const [msgs, setMsgs] = useState<Msg[]>([]);
	const [loading, setLoading] = useState(false);
	const [streaming, setStreaming] = useState(false);
	const [error, setError] = useState<Error | null>(null);

	const msgsRef = useRef<Msg[]>([]);
	const currentReplyRef = useRef<Msg | null>(null);
	const abortRef = useRef<AbortController | null>(null);
	const rafRef = useRef<number | null>(null);

	const scheduleUpdate = useCallback(() => {
		if (rafRef.current !== null) return;
		rafRef.current = requestAnimationFrame(() => {
			rafRef.current = null;
			setMsgs([...msgsRef.current]);
		});
	}, []);

	const processEvent = useCallback(
		(event: AgentEvent) => {
			if (event.type === EventType.REPLY_START) {
				const e = event as ReplyStartEvent;
				const msg = AssistantMsg({ id: e.reply_id, name: e.name, content: [] });
				msgsRef.current = [...msgsRef.current, msg];
				currentReplyRef.current = msg;
			} else if (currentReplyRef.current) {
				appendEvent(currentReplyRef.current, event);
			}
			scheduleUpdate();
		},
		[scheduleUpdate],
	);

	// Load history when sessionId changes
	useEffect(() => {
		msgsRef.current = [];
		currentReplyRef.current = null;
		setMsgs([]);
		setError(null);

		if (!agentId || !sessionId) return;

		let cancelled = false;
		setLoading(true);
		(async () => {
			try {
				const { messages, is_running } = await sessionApi.messages(sessionId, agentId);
				if (cancelled) return;
				msgsRef.current = messages;
				setStreaming(is_running);
				scheduleUpdate();
			} catch (e) {
				if (!cancelled) setError(e as Error);
			} finally {
				if (!cancelled) setLoading(false);
			}
		})();

		return () => {
			cancelled = true;
		};
	}, [agentId, sessionId, scheduleUpdate]);

	const runStream = useCallback(
		async (request: ChatRequest) => {
			abortRef.current?.abort();
			const controller = new AbortController();
			abortRef.current = controller;

			setStreaming(true);
			setError(null);

			try {
				for await (const event of chatApi.stream(request, controller.signal)) {
					processEvent(event);
				}
			} catch (e) {
				if ((e as Error).name !== 'AbortError') setError(e as Error);
			} finally {
				setStreaming(false);
				currentReplyRef.current = null;
			}
		},
		[processEvent],
	);

	const send = useCallback(
		async (content: ContentBlock[]) => {
			if (!agentId || !sessionId) return;

			const userMsg = UserMsg({ name: 'user', content });
			msgsRef.current = [...msgsRef.current, userMsg];
			scheduleUpdate();

			await runStream({ agent_id: agentId, session_id: sessionId, input: userMsg });
		},
		[agentId, sessionId, scheduleUpdate, runStream],
	);

	const onUserConfirm = useCallback(
		async (
			toolCall: ToolCallBlock,
			confirm: boolean,
			replyId: string,
			rules?: ToolCallBlock['suggested_rules'],
		) => {
			if (!agentId || !sessionId) return;

			// Restore the ref so continuation events (no REPLY_START) have a target
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

			await runStream({ agent_id: agentId, session_id: sessionId, input: event });
		},
		[agentId, sessionId, runStream],
	);

	const abort = useCallback(() => {
		abortRef.current?.abort();
	}, []);

	return { msgs, loading, streaming, error, send, onUserConfirm, abort };
}
