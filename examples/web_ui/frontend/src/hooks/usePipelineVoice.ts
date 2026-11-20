import type { AgentEvent } from '@agentscope-ai/agentscope/event';
import { useCallback, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { toast } from 'sonner';

import { chatApi, sessionApi } from '@/api';
import type { ASRModelConfig } from '@/api';
import { getBaseUrl, getUserId } from '@/api/client';
import { useAudioManager, useReplayController } from '@/context/AudioContext';
import { useTranslation } from '@/i18n/useI18n';
import { getPendingPipelineVoice, retainPipelineVoice } from '@/lib/pipelineVoiceCancellation';
import {
	PipelineVoiceSession,
	type PipelineVoiceOptions,
	type PipelineVoiceState,
} from '@/lib/pipelineVoiceSession';

interface Options {
	agentId: string | null;
	sessionId: string | null;
	asr: ASRModelConfig | null;
	realtimeASR: boolean;
	canStart: boolean;
	send: PipelineVoiceOptions['send'];
}

/** Bind the voice lifecycle to the current viewport without owning Chat state. */
export function usePipelineVoice(options: Options) {
	const { t } = useTranslation();
	const audioManager = useAudioManager();
	const replay = useReplayController();
	const [state, setState] = useState<PipelineVoiceState>('idle');
	const [transcript, setTranscript] = useState('');
	const sessionRef = useRef<PipelineVoiceSession | null>(null);
	const suppressedRef = useRef(new Set<string>());
	const optionsRef = useRef(options);
	const translationRef = useRef(t);
	const unsubscribeRef = useRef<(() => void) | null>(null);
	const baseUrl = getBaseUrl();
	const userId = getUserId();
	// Both the session callbacks and this scope's cleanup retain this snapshot.
	const requestScope = useMemo(() => ({ baseUrl, userId }), [baseUrl, userId]);
	const scopeKey = JSON.stringify([baseUrl, userId, options.agentId, options.sessionId]);
	useLayoutEffect(() => {
		optionsRef.current = options;
		translationRef.current = t;
	});
	const observe = useCallback((session: PipelineVoiceSession) => {
		unsubscribeRef.current?.();
		sessionRef.current = session;
		suppressedRef.current = session.suppressedReplies;
		setState(session.snapshot.state);
		setTranscript(session.snapshot.transcript);
		unsubscribeRef.current = session.subscribe({
			onState: (next, text) => {
				setState(next);
				setTranscript(text);
			},
			onError: (error) =>
				toast.error(translationRef.current('pipelineVoice.failed'), {
					description: error instanceof Error ? error.message : String(error),
				}),
		});
	}, []);

	useLayoutEffect(() => {
		const { agentId, sessionId } = optionsRef.current;
		setState('idle');
		setTranscript('');
		suppressedRef.current = new Set();
		const pending = getPendingPipelineVoice(scopeKey);
		if (pending) observe(pending);
		return () => {
			const session = sessionRef.current;
			unsubscribeRef.current?.();
			unsubscribeRef.current = null;
			sessionRef.current = null;
			if (session && agentId && sessionId) {
				retainPipelineVoice(scopeKey, session, (signal) =>
					sessionApi.streamEvents(
						sessionId,
						agentId,
						signal,
						undefined,
						undefined,
						requestScope,
					),
				);
			}
		};
	}, [scopeKey, options.agentId, options.sessionId, observe, requestScope]);

	const stop = useCallback(async () => {
		await sessionRef.current?.stop();
	}, []);
	const onEvent = useCallback(
		(event: AgentEvent) => {
			const pending = getPendingPipelineVoice(scopeKey);
			if (pending) return pending.stoppedPlayback(event);
			return sessionRef.current?.onEvent(event) ?? false;
		},
		[scopeKey],
	);
	const busy = state !== 'idle' && state !== 'error';
	const toggle = useCallback(async () => {
		if (busy || sessionRef.current?.restartBlocked) {
			await stop();
			return;
		}
		const current = optionsRef.current;
		if (!current.canStart || !current.asr || !current.agentId || !current.sessionId) return;
		replay?.stop();
		audioManager?.stopAllPlayback();
		try {
			const { agentId, sessionId } = current;
			const session = new PipelineVoiceSession({
				scope: requestScope,
				asr: current.asr,
				realtimeASR: current.realtimeASR,
				suppressedReplies: suppressedRef.current,
				send: current.send,
				createSubmission: (signal) =>
					chatApi.createSubmission(agentId, sessionId, {
						...requestScope,
						signal,
						silent: true,
					}),
				cancelSubmission: (id, signal) =>
					chatApi.cancelSubmission(id, agentId, sessionId, {
						...requestScope,
						signal,
						silent: true,
					}),
				deleteQueued: async (id, signal) => {
					await chatApi.deleteQueued(id, agentId, sessionId, {
						...requestScope,
						signal,
						silent: true,
					});
				},
				interrupt: async (replyId, signal) => {
					await sessionApi.interrupt(sessionId, agentId, replyId, {
						...requestScope,
						signal,
						silent: true,
					});
				},
				inputResult: (messageId, submissionId, signal) =>
					sessionApi.inputResult(sessionId, agentId, messageId, submissionId, {
						...requestScope,
						signal,
						silent: true,
					}),
				canListen: () => optionsRef.current.canStart,
			});
			observe(session);
			await session.start();
		} catch (error) {
			setState('error');
			toast.error(t('pipelineVoice.failed'), {
				description: error instanceof Error ? error.message : String(error),
			});
		}
	}, [audioManager, replay, busy, stop, t, observe, requestScope]);

	return { state, transcript, busy, toggle, stop, onEvent };
}
