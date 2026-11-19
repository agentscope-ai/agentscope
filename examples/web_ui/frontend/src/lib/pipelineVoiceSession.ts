import {
	EventType,
	ReplyFinishedReason,
	type AgentEvent,
	type CustomEvent,
	type ReplyStartEvent,
} from '@agentscope-ai/agentscope/event';
import type { ContentBlock } from '@agentscope-ai/agentscope/message';
import type { MicVAD } from '@ricky0123/vad-web';

import { asrApi } from '@/api/asr';
import { ApiError, type ApiScope } from '@/api/client';
import type { InputResult } from '@/api/session';
import type { ASRModelConfig } from '@/api/types';
import { BrowserRealtimeASRTransport } from '@/lib/browserRealtimeASRTransport';
import { PipelineAudioPlayer } from '@/lib/pipelineAudioPlayer';

export type PipelineVoiceState =
	| 'idle'
	| 'connecting'
	| 'listening'
	| 'transcribing'
	| 'thinking'
	| 'speaking'
	| 'awaiting_confirmation'
	| 'stopping'
	| 'error';

export interface PipelineVoiceOptions {
	scope: ApiScope;
	asr: ASRModelConfig;
	realtimeASR: boolean;
	suppressedReplies: Set<string>;
	send: (
		blocks: ContentBlock[],
		observer: {
			scope: ApiScope;
			submissionId: string;
			signal: AbortSignal;
			isCurrent: () => boolean;
			hasExecutionEvidence: () => boolean;
			submitted: (id: string) => void;
			accepted: (id: string | null) => void;
		},
	) => Promise<void>;
	createSubmission: (signal: AbortSignal) => Promise<{ submission_id: string }>;
	cancelSubmission: (
		id: string,
		signal: AbortSignal,
	) => Promise<{
		state: 'cancelled' | 'started';
		queue_item_id: string | null;
	}>;
	interrupt: (replyId: string, signal: AbortSignal) => Promise<void>;
	deleteQueued: (id: string, signal: AbortSignal) => Promise<void>;
	findQueued: (messageId: string, signal: AbortSignal) => Promise<string | null>;
	inputResult: (
		messageId: string,
		submissionId: string,
		signal: AbortSignal,
	) => Promise<InputResult>;
	onState: (state: PipelineVoiceState, transcript: string) => void;
	onError: (error: unknown) => void;
	canListen: () => boolean;
}

/** One browser-owned, half-duplex conversation; Chat owns persistence and tools. */
export class PipelineVoiceSession {
	private readonly options: PipelineVoiceOptions;
	private readonly context: AudioContext;
	private readonly player: PipelineAudioPlayer;
	private readonly abort = new AbortController();
	private vad: MicVAD | null = null;
	private vadCreation: Promise<MicVAD> | null = null;
	private vadStart: Promise<void> | null = null;
	private stream: MediaStream | null = null;
	private asr: BrowserRealtimeASRTransport | null = null;
	private state: PipelineVoiceState = 'connecting';
	private transcript = '';
	private stopped = false;
	private cancelOnStop = true;
	private userMessageId: string | null = null;
	private submissionId: string | null = null;
	private submissionPending = false;
	private submissionAttempt: { controller: AbortController; terminal: boolean } | null = null;
	private queueId: string | null = null;
	private inputStarted = false;
	private inputFinished = false;
	private cancellationPending = false;
	private cancellationTimer: ReturnType<typeof setTimeout> | undefined;
	private cleanupDone = false;
	private replyId: string | null = null;
	private replyEnded = false;
	private resumeScheduled = false;
	private waitingForConfirmation = false;
	private speechTimer: ReturnType<typeof setTimeout> | null = null;
	private transitionTimer: ReturnType<typeof setTimeout> | null = null;
	private cleanupPromise: Promise<void> | null = null;
	private cancellationPromise: Promise<void> | null = null;
	private cancellationController: AbortController | null = null;
	private observers = new Set<Pick<PipelineVoiceOptions, 'onState' | 'onError'>>();

	constructor(options: PipelineVoiceOptions) {
		this.options = options;
		// Construct and resume within the user's click, before loading VAD assets.
		this.context = new AudioContext();
		this.player = new PipelineAudioPlayer(
			this.context,
			(busy) => {
				if (this.stopped) return;
				if (busy)
					this.setState(
						this.waitingForConfirmation ? 'awaiting_confirmation' : 'speaking',
					);
				else this.maybeListen();
			},
			(error) => this.fail(error),
		);
	}

	async start(): Promise<void> {
		this.setState('connecting');
		try {
			await this.context.resume();
			this.context.onstatechange = () => {
				if (!this.stopped && this.context.state === 'suspended') {
					this.fail(new Error('Audio was suspended. Restart voice mode to continue.'));
				}
			};
			const stream = await navigator.mediaDevices.getUserMedia({
				audio: {
					channelCount: 1,
					echoCancellation: true,
					noiseSuppression: true,
					autoGainControl: true,
				},
			});
			if (this.stopped) {
				stream.getTracks().forEach((track) => track.stop());
				return;
			}
			this.stream = stream;
			stream.getAudioTracks().forEach((track) => {
				track.onended = () => this.fail(new Error('Microphone disconnected.'));
			});
			const { MicVAD } = await import('@ricky0123/vad-web');
			if (this.stopped) return;
			this.vadCreation = MicVAD.new({
				model: 'v5',
				startOnLoad: false,
				audioContext: this.context,
				baseAssetPath: `${import.meta.env.BASE_URL}voice/`,
				onnxWASMBasePath: `${import.meta.env.BASE_URL}voice/`,
				ortConfig: (ort) => {
					ort.env.wasm.numThreads = 1;
				},
				redemptionMs: 900,
				minSpeechMs: 300,
				preSpeechPadMs: 300,
				submitUserSpeechOnPause: false,
				getStream: async () => stream,
				pauseStream: async () => {
					stream.getTracks().forEach((track) => {
						track.enabled = false;
					});
				},
				resumeStream: async () => {
					stream.getTracks().forEach((track) => {
						track.enabled = true;
					});
					return stream;
				},
				onSpeechStart: () => {
					if (this.stopped || this.state !== 'listening') return;
					this.clearSpeechTimer();
					this.speechTimer = setTimeout(
						() => this.fail(new Error('Please keep each voice turn under 60 seconds.')),
						60_000,
					);
				},
				onVADMisfire: () => this.clearSpeechTimer(),
				onFrameProcessed: (_, frame) => {
					if (this.state !== 'listening' || !this.asr) return;
					const bytes = new ArrayBuffer(frame.length * 2);
					const view = new DataView(bytes);
					frame.forEach((sample, index) => {
						const clamped = Math.max(-1, Math.min(1, sample));
						view.setInt16(
							index * 2,
							clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff,
							true,
						);
					});
					this.asr.sendAudio(bytes);
				},
				onSpeechEnd: (audio) => {
					void this.submitSpeech(audio);
				},
			});
			this.vad = await this.vadCreation;
			if (this.stopped) return;
			await this.listen();
		} catch (error) {
			this.fail(error);
		}
	}

	/** Whether this instance still owns microphone capture. */
	get active(): boolean {
		return !this.stopped;
	}

	get snapshot() {
		return { state: this.state, transcript: this.transcript };
	}

	get suppressedReplies(): Set<string> {
		return this.options.suppressedReplies;
	}

	get needsCancellation(): boolean {
		return this.cancellationPending;
	}

	subscribe(observer: Pick<PipelineVoiceOptions, 'onState' | 'onError'>): () => void {
		this.observers.add(observer);
		return () => {
			this.observers.delete(observer);
		};
	}

	/** Reconcile unknown replies through the submission, never replayed Msg IDs. */
	cancellationEventHandler(): (event: AgentEvent) => void {
		return (event) => {
			if (event.type === EventType.CUSTOM) {
				const value = (event.value ?? {}) as { queue_item_id?: string };
				if (!this.queueId || value.queue_item_id !== this.queueId) return;
				this.onEvent(event);
			} else if (event.reply_id === this.replyId) {
				this.onEvent(event);
			}
		};
	}

	/** Keep receiving this turn's events until both cleanup and cancellation finish. */
	get restartBlocked(): boolean {
		return (
			this.active ||
			!this.cleanupDone ||
			this.cancellationPending ||
			!!this.cancellationPromise
		);
	}

	/** Return true for audio owned by this session, including cancelled replies. */
	onEvent(event: AgentEvent): boolean | Promise<void> {
		const handled =
			!this.stopped ||
			('reply_id' in event && this.options.suppressedReplies.has(event.reply_id));
		if (event.type === EventType.CUSTOM) {
			const custom = event as CustomEvent;
			const value = (custom.value ?? {}) as {
				message_ids?: string[];
				reply_id?: string;
				queue_item_id?: string;
			};
			if (this.stopped && (!this.queueId || value.queue_item_id !== this.queueId))
				return handled;
			if (
				custom.name === 'chat_input_started' &&
				this.userMessageId &&
				value.message_ids?.includes(this.userMessageId)
			)
				this.inputStarted = true;
			if (
				!this.stopped &&
				custom.name === 'tts_error' &&
				this.userMessageId &&
				(value.message_ids?.includes(this.userMessageId) ||
					(!!this.replyId && value.reply_id === this.replyId))
			)
				this.fail(
					new Error('Speech synthesis failed. The text reply remains available.'),
					false,
				);
			if (
				['chat_input_failed', 'chat_input_cancelled'].includes(custom.name) &&
				this.userMessageId &&
				value.message_ids?.includes(this.userMessageId)
			) {
				this.finishCancellation();
				this.fail(new Error('The voice turn could not be completed.'));
			}
			return handled;
		}
		if (
			event.type === EventType.REPLY_START &&
			(event as ReplyStartEvent).role === 'assistant'
		) {
			if (!this.stopped && this.inputStarted && !this.inputFinished && !this.replyId) {
				this.replyId = event.reply_id;
				this.options.suppressedReplies.add(event.reply_id);
				if (this.cancellationPending) this.continueCancellation();
			} else if (!this.stopped && event.reply_id !== this.replyId) {
				this.fail(new Error('Another reply started. Restart voice mode when it finishes.'));
			}
			if (!this.stopped && event.reply_id === this.replyId) {
				this.clearTransitionTimer();
				this.waitingForConfirmation = false;
				this.setState('thinking');
			}
		}
		if (!this.replyId || event.reply_id !== this.replyId) return handled;
		if (this.stopped) {
			if (event.type === EventType.REPLY_END) this.finishCancellation();
			return handled;
		}
		switch (event.type) {
			case EventType.DATA_BLOCK_START:
				if (event.media_type.startsWith('audio/'))
					this.player.start(event.block_id, event.media_type);
				break;
			case EventType.DATA_BLOCK_DELTA:
				if (event.media_type.startsWith('audio/'))
					this.player.append(event.block_id, event.data);
				break;
			case EventType.DATA_BLOCK_END:
				return this.player.end(event.block_id) ?? handled;
			case EventType.REQUIRE_USER_CONFIRM:
			case EventType.REQUIRE_EXTERNAL_EXECUTION:
				this.waitingForConfirmation = true;
				this.setState('awaiting_confirmation');
				break;
			case EventType.REPLY_END:
				this.completeSubmission();
				this.replyEnded = true;
				this.waitingForConfirmation = false;
				if (event.finished_reason !== ReplyFinishedReason.COMPLETED) {
					this.fail(new Error(event.error?.message ?? 'The reply was interrupted.'));
					break;
				}
				this.maybeListen();
				break;
		}
		return handled;
	}

	/** Stop microphone and playback immediately, then cancel only this turn. */
	stop(cancelReply = true): Promise<void> {
		if (!this.cleanupPromise) {
			this.stopped = true;
			this.cancelOnStop = cancelReply;
			this.cancellationPending =
				cancelReply && !!this.userMessageId && !this.inputFinished && !this.replyEnded;
			this.setState('stopping');
			this.abort.abort();
			this.submissionAttempt?.controller.abort();
			this.clearSpeechTimer();
			this.clearTransitionTimer();
			if (this.cancellationPending) {
				this.transitionTimer = setTimeout(() => {
					this.reportError(
						new Error(
							'Still waiting for the voice turn to end. Click End voice again to retry cancellation.',
						),
					);
				}, 60_000);
			}
			this.asr?.close();
			this.asr = null;
			this.player.stop();
			this.stream?.getTracks().forEach((track) => {
				track.onended = null;
				track.stop();
			});
			this.cleanupPromise = (async () => {
				try {
					// Creation/start may still be using the shared audio context.
					const vad = this.vadCreation
						? await this.vadCreation.catch(() => null)
						: this.vad;
					await this.vadStart?.catch(() => undefined);
					await vad?.destroy();
				} catch (error) {
					this.reportError(error);
				} finally {
					await this.context.close().catch((error) => this.reportError(error));
				}
				this.cleanupDone = true;
				this.maybeFinishStop();
			})();
		}
		return Promise.all([this.cleanupPromise, this.cancelTurn(true)]).then(() => undefined);
	}

	/** New queue/reply information must be handled even during an earlier request. */
	private continueCancellation(): void {
		void (this.cancellationPromise ?? Promise.resolve()).then(() => this.cancelTurn());
	}

	private cancelTurn(retry = false): Promise<void> {
		if (!this.cancellationPending) return Promise.resolve();
		if (this.cancellationPromise && !retry) return this.cancellationPromise;
		this.cancellationController?.abort();
		clearTimeout(this.cancellationTimer);
		const controller = new AbortController();
		const { signal } = controller;
		this.cancellationController = controller;
		const deadline = setTimeout(
			() =>
				controller.abort(new Error('Voice cancellation timed out. Retrying cancellation.')),
			10_000,
		);
		// Bound the attempt even if a transport does not settle after abort.
		const aborted = new Promise<never>((_, reject) => {
			signal.addEventListener('abort', () => reject(signal.reason), { once: true });
		});
		const work = Promise.resolve().then(async () => {
			signal.throwIfAborted();
			if (!this.cancellationPending) return;
			if (this.submissionId) {
				const result = await this.options.cancelSubmission(this.submissionId, signal);
				signal.throwIfAborted();
				if (result.state === 'cancelled') {
					// A retired receipt can belong to an already completed reply.
					if (this.userMessageId) {
						const input = await this.options.inputResult(
							this.userMessageId,
							this.submissionId,
							signal,
						);
						signal.throwIfAborted();
						if (input.reply_id && (!this.replyId || input.reply_id === this.replyId))
							this.options.suppressedReplies.add(input.reply_id);
					}
					this.finishCancellation();
					return;
				}
				if (!this.queueId) this.queueId = result.queue_item_id;
			}
			if (this.userMessageId && this.submissionId) {
				const result = await this.options.inputResult(
					this.userMessageId,
					this.submissionId,
					signal,
				);
				signal.throwIfAborted();
				if (this.replyId && result.reply_id && result.reply_id !== this.replyId)
					throw new Error('Voice submission returned a different reply.');
				if (result.reply_id) this.options.suppressedReplies.add(result.reply_id);
				if (!this.cancellationPending) return;
				if (result.state === 'finished') {
					this.finishCancellation();
					return;
				}
				if (result.state === 'reply' && result.reply_id && !this.replyId) {
					this.replyId = result.reply_id;
					this.inputStarted = true;
				}
			}
			if (!this.queueId && !this.inputStarted && !this.replyId && this.userMessageId) {
				const id = await this.options.findQueued(this.userMessageId, signal);
				signal.throwIfAborted();
				// Acceptance may arrive while the queue snapshot is being fetched.
				if (!this.queueId) this.queueId = id;
			}
			if (!this.cancellationPending) return;
			if (this.replyId) {
				// 202 accepts a request; only a terminal event or result ends cancellation.
				await this.options.interrupt(this.replyId, signal);
			} else if (this.queueId && !this.inputStarted && !this.replyId) {
				await this.options.deleteQueued(this.queueId, signal);
				signal.throwIfAborted();
				this.finishCancellation();
			}
		});
		this.cancellationPromise = Promise.race([work, aborted])
			.catch((error) => {
				if (this.cancellationController !== controller || !this.cancellationPending) return;
				if (
					error instanceof ApiError &&
					error.status === 404 &&
					error.code === 'session_not_found'
				) {
					this.finishCancellation();
					return;
				}
				this.reportError(error);
			})
			.finally(() => {
				clearTimeout(deadline);
				controller.abort();
				// An older attempt must never clear or reschedule its replacement.
				if (this.cancellationController !== controller) return;
				this.cancellationController = null;
				this.cancellationPromise = null;
				clearTimeout(this.cancellationTimer);
				if (this.cancellationPending) {
					this.cancellationTimer = setTimeout(() => void this.cancelTurn(), 2000);
				}
				this.maybeFinishStop();
			});
		return this.cancellationPromise;
	}

	private maybeFinishStop(): void {
		if (this.stopped && !this.restartBlocked) this.setState('idle');
	}

	private finishCancellation(): void {
		this.completeSubmission();
		this.inputFinished = true;
		this.cancellationPending = false;
		this.cancellationController?.abort();
		clearTimeout(this.cancellationTimer);
		this.clearTransitionTimer();
		this.maybeFinishStop();
	}

	private async listen(): Promise<void> {
		if (this.stopped) return;
		if (!this.options.canListen())
			throw new Error('The session is busy. Restart voice mode when it is ready.');
		this.replyId = null;
		this.userMessageId = null;
		this.submissionId = null;
		this.queueId = null;
		this.inputStarted = false;
		this.inputFinished = false;
		this.replyEnded = false;
		this.resumeScheduled = false;
		this.transcript = '';
		this.setState('connecting');
		if (this.options.realtimeASR) {
			const config = this.options.asr;
			this.asr = new BrowserRealtimeASRTransport({
				scope: this.options.scope,
				config: {
					credentialId: config.credential_id,
					model: config.model,
					parameters: config.parameters,
				},
				onTranscript: (text) => {
					this.transcript = text;
					if (!this.stopped) this.setState(this.state);
				},
				onError: (error) => this.fail(error),
			});
			await this.asr.connect(16000);
		}
		if (this.stopped) return;
		this.vadStart = this.vad?.start() ?? null;
		await this.vadStart;
		if (this.stopped) return;
		this.setState('listening');
	}

	private async submitSpeech(audio: Float32Array): Promise<void> {
		if (this.stopped || this.state !== 'listening') return;
		const attempt = { controller: new AbortController(), terminal: false };
		this.submissionAttempt = attempt;
		let deadline: ReturnType<typeof setTimeout> | undefined;
		this.setState('transcribing');
		this.clearSpeechTimer();
		try {
			await this.vad?.pause();
			if (this.stopped) return;
			if (this.asr) {
				await this.asr.stop();
				this.asr.close();
				this.asr = null;
			} else {
				const { utils } = await import('@ricky0123/vad-web');
				const config = this.options.asr;
				const result = await asrApi.transcribe({
					scope: this.options.scope,
					file: new File([utils.encodeWAV(audio, 1, 16000, 1, 16)], 'speech.wav', {
						type: 'audio/wav',
					}),
					credentialId: config.credential_id,
					model: config.model,
					parameters: config.parameters,
					signal: this.abort.signal,
				});
				this.transcript = result.text;
			}
			if (this.stopped) return;
			const text = this.transcript.trim();
			if (!text) {
				await this.listen();
				return;
			}
			this.setState('thinking');
			this.submissionPending = true;
			this.transitionTimer = setTimeout(
				() => this.fail(new Error('Timed out waiting for the chat reply.')),
				60_000,
			);
			deadline = setTimeout(
				() => attempt.controller.abort(new Error('Timed out submitting the voice turn.')),
				30_000,
			);
			const { signal } = attempt.controller;
			const aborted = new Promise<never>((_, reject) => {
				signal.addEventListener('abort', () => reject(signal.reason), { once: true });
			});
			let accepted = false;
			try {
				const work = async () => {
					const reservation = await this.options.createSubmission(signal);
					signal.throwIfAborted();
					this.submissionId = reservation.submission_id;
					await this.options.send(
						[
							{
								type: 'text',
								text,
								id: crypto.randomUUID(),
								created_at: new Date().toISOString(),
							},
						],
						{
							submissionId: this.submissionId,
							scope: this.options.scope,
							signal,
							isCurrent: () =>
								this.submissionAttempt === attempt && !attempt.terminal,
							hasExecutionEvidence: () =>
								attempt.terminal ||
								(this.submissionAttempt === attempt &&
									(this.inputStarted || !!this.replyId)),
							submitted: (id) => {
								if (this.submissionAttempt !== attempt || signal.aborted) return;
								this.userMessageId = id;
							},
							accepted: (id) => {
								if (this.submissionAttempt !== attempt || signal.aborted) return;
								accepted = true;
								this.queueId = id;
								if (this.stopped && this.cancelOnStop) this.continueCancellation();
							},
						},
					);
				};
				await Promise.race([work(), aborted]);
			} catch (error) {
				if (this.submissionAttempt !== attempt || attempt.terminal) return;
				// Only explicit rejection, without acceptance/execution evidence, proves
				// no turn exists. Network errors, timeouts and 5xx remain ambiguous.
				if (
					error instanceof ApiError &&
					[400, 401, 403, 404, 422, 429].includes(error.status) &&
					!accepted &&
					!this.queueId &&
					!this.inputStarted &&
					!this.replyId
				) {
					this.finishCancellation();
					this.fail(error);
					return;
				}
				throw error;
			}
		} catch (error) {
			if (this.submissionAttempt !== attempt || attempt.terminal) return;
			if (this.stopped) {
				if (this.userMessageId) this.reportError(error);
				if (this.cancellationPending) this.continueCancellation();
			} else this.fail(error);
		} finally {
			clearTimeout(deadline);
			if (this.submissionAttempt === attempt) {
				this.submissionPending = false;
				this.maybeListen();
			}
		}
	}

	private completeSubmission(): void {
		if (this.submissionAttempt) {
			this.submissionAttempt.terminal = true;
			this.submissionAttempt.controller.abort();
		}
		this.submissionPending = false;
	}

	private maybeListen(): void {
		if (
			this.stopped ||
			!this.replyEnded ||
			this.player.busy ||
			this.waitingForConfirmation ||
			this.resumeScheduled ||
			this.submissionPending
		)
			return;
		// Do not re-enter from multiple audio completion callbacks.
		this.resumeScheduled = true;
		this.clearTransitionTimer();
		this.transitionTimer = setTimeout(() => {
			void this.listen().catch((error) => this.fail(error));
		}, 250);
	}

	private setState(state: PipelineVoiceState): void {
		this.state = state;
		this.options.onState(state, this.transcript);
		for (const observer of this.observers) observer.onState(state, this.transcript);
	}
	private reportError(error: unknown): void {
		this.options.onError(error);
		for (const observer of this.observers) observer.onError(error);
	}
	private clearSpeechTimer(): void {
		if (this.speechTimer) clearTimeout(this.speechTimer);
		this.speechTimer = null;
	}
	private clearTransitionTimer(): void {
		if (this.transitionTimer) clearTimeout(this.transitionTimer);
		this.transitionTimer = null;
	}
	private fail(error: unknown, cancelReply = true): void {
		if (this.stopped) return;
		this.reportError(error);
		void this.stop(cancelReply).then(() => {
			if (!this.restartBlocked) this.setState('error');
		});
	}
}
