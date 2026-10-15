import { useCallback, useEffect, useRef, useState } from 'react';

import {
	BrowserRealtimeASRTransport,
	type RealtimeASRConfig,
} from '@/lib/browserRealtimeASRTransport';

export type RealtimeAudioRecorderState = 'idle' | 'requesting' | 'recording' | 'transcribing';

interface UseRealtimeAudioRecorderOptions {
	config: RealtimeASRConfig | null;
	resetKey: string;
	onStart: () => void;
	onTranscript: (text: string, isLast: boolean) => void;
	onError: (error: unknown) => void;
}

/** Capture mono PCM16 and stream it to the selected realtime ASR model. */
export function useRealtimeAudioRecorder({
	config,
	resetKey,
	onStart,
	onTranscript,
	onError,
}: UseRealtimeAudioRecorderOptions) {
	const [state, setState] = useState<RealtimeAudioRecorderState>('idle');
	const generationRef = useRef(0);
	const streamRef = useRef<MediaStream | null>(null);
	const contextRef = useRef<AudioContext | null>(null);
	const sourceRef = useRef<MediaStreamAudioSourceNode | null>(null);
	const workletRef = useRef<AudioWorkletNode | null>(null);
	const flushResolveRef = useRef<(() => void) | null>(null);
	const transportRef = useRef<BrowserRealtimeASRTransport | null>(null);
	const callbacksRef = useRef({ onStart, onTranscript, onError });

	useEffect(() => {
		callbacksRef.current = { onStart, onTranscript, onError };
	}, [onStart, onTranscript, onError]);

	const supported =
		typeof navigator !== 'undefined' &&
		!!navigator.mediaDevices?.getUserMedia &&
		typeof AudioContext !== 'undefined' &&
		'audioWorklet' in AudioContext.prototype;

	const stopCapture = useCallback(() => {
		workletRef.current?.disconnect();
		workletRef.current = null;
		sourceRef.current?.disconnect();
		sourceRef.current = null;
		streamRef.current?.getTracks().forEach((track) => track.stop());
		streamRef.current = null;
		const context = contextRef.current;
		contextRef.current = null;
		if (context && context.state !== 'closed') void context.close();
	}, []);

	const reset = useCallback(() => {
		generationRef.current += 1;
		stopCapture();
		transportRef.current?.close();
		transportRef.current = null;
		setState('idle');
	}, [stopCapture]);

	useEffect(() => {
		reset();
		return reset;
	}, [reset, resetKey]);

	const start = useCallback(async () => {
		if (!supported || !config || state !== 'idle') return;
		const generation = ++generationRef.current;
		setState('requesting');
		callbacksRef.current.onStart();
		try {
			const stream = await navigator.mediaDevices.getUserMedia({
				audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
			});
			if (generation !== generationRef.current) {
				stream.getTracks().forEach((track) => track.stop());
				return;
			}
			streamRef.current = stream;

			const context = new AudioContext();
			contextRef.current = context;
			await context.audioWorklet.addModule(
				`${import.meta.env.BASE_URL}pcm-recorder-worklet.js`,
			);
			const transport = new BrowserRealtimeASRTransport({
				config,
				onTranscript: (text, isLast) => {
					if (generation === generationRef.current) {
						callbacksRef.current.onTranscript(text, isLast);
					}
				},
			});
			transportRef.current = transport;
			await transport.connect(context.sampleRate);
			if (generation !== generationRef.current) return;

			const source = context.createMediaStreamSource(stream);
			const worklet = new AudioWorkletNode(context, 'pcm-recorder');
			const silentOutput = context.createGain();
			silentOutput.gain.value = 0;
			worklet.port.onmessage = (event: MessageEvent<ArrayBuffer | { type: string }>) => {
				if (event.data instanceof ArrayBuffer) {
					transport.sendAudio(event.data);
				} else if (event.data.type === 'flushed') {
					flushResolveRef.current?.();
					flushResolveRef.current = null;
				}
			};
			source.connect(worklet);
			worklet.connect(silentOutput).connect(context.destination);
			sourceRef.current = source;
			workletRef.current = worklet;
			await context.resume();
			setState('recording');
		} catch (error) {
			if (generation === generationRef.current) {
				stopCapture();
				transportRef.current?.close();
				transportRef.current = null;
				setState('idle');
				callbacksRef.current.onError(error);
			}
		}
	}, [config, state, stopCapture, supported]);

	const stop = useCallback(async () => {
		if (state !== 'recording') return;
		const generation = generationRef.current;
		const transport = transportRef.current;
		setState('transcribing');
		const worklet = workletRef.current;
		if (worklet) {
			await new Promise<void>((resolve) => {
				const timeout = window.setTimeout(resolve, 100);
				flushResolveRef.current = () => {
					window.clearTimeout(timeout);
					resolve();
				};
				worklet.port.postMessage({ type: 'flush' });
			});
			flushResolveRef.current = null;
		}
		stopCapture();
		try {
			await transport?.stop();
		} catch (error) {
			if (generation === generationRef.current) {
				callbacksRef.current.onError(error);
			}
		} finally {
			if (generation === generationRef.current) {
				transport?.close();
				transportRef.current = null;
				setState('idle');
			}
		}
	}, [state, stopCapture]);

	const toggle = useCallback(() => {
		if (state === 'recording') void stop();
		else if (state === 'idle') void start();
	}, [start, state, stop]);

	return { state, supported, toggle };
}
