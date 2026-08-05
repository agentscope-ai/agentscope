import { useEffect, useRef, useState } from 'react';

/**
 * Captures audio from the user's microphone and delivers 16-bit mono PCM
 * chunks as base64 strings at approximately 100 ms intervals.
 *
 * When ``enabled`` is ``true`` the hook requests microphone access, creates
 * an AudioContext, and connects a ScriptProcessorNode that downsamples from
 * the browser's native sample rate to ``targetSampleRate`` (default 16 kHz).
 * Each chunk is Int16 little-endian, base64-encoded, and passed to
 * ``onChunk``.
 *
 * When ``enabled`` flips to ``false``, all resources are released.
 *
 * @returns ``{ active, error }`` — ``active`` is ``true`` while the
 *   microphone is actually streaming; ``error`` captures any getUserMedia
 *   or AudioContext failure.
 */
export function useMicrophone(
	onChunk: (base64Pcm: string) => void,
	enabled: boolean,
	targetSampleRate = 16000,
) {
	const [active, setActive] = useState(false);
	const [error, setError] = useState<Error | null>(null);

	const onChunkRef = useRef(onChunk);
	useEffect(() => {
		onChunkRef.current = onChunk;
	}, [onChunk]);

	useEffect(() => {
		if (!enabled) {
			setActive(false);
			return;
		}

		let cancelled = false;
		let audioCtx: AudioContext | null = null;
		let stream: MediaStream | null = null;

		(async () => {
			try {
				stream = await navigator.mediaDevices.getUserMedia({
					audio: {
						channelCount: 1,
						echoCancellation: true,
						noiseSuppression: true,
					},
				});
				if (cancelled) {
					stream.getTracks().forEach((t) => t.stop());
					return;
				}

				audioCtx = new AudioContext();
				const source = audioCtx.createMediaStreamSource(stream);
				const nativeRate = audioCtx.sampleRate;
				const ratio = nativeRate / targetSampleRate;

				// ScriptProcessorNode: bufferSize 4096 ≈ ~93 ms at 44100 Hz
				const processor = audioCtx.createScriptProcessor(4096, 1, 1);

				processor.onaudioprocess = (e: AudioProcessingEvent) => {
					const inputData = e.inputBuffer.getChannelData(0);

					// Downsample to target rate
					const outputLength = Math.floor(inputData.length / ratio);
					const int16 = new Int16Array(outputLength);
					for (let i = 0; i < outputLength; i++) {
						const srcIndex = Math.floor(i * ratio);
						const sample = Math.max(-1, Math.min(1, inputData[srcIndex]));
						int16[i] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
					}

					// Base64 encode
					const bytes = new Uint8Array(int16.buffer);
					let binary = '';
					for (let j = 0; j < bytes.byteLength; j++) {
						binary += String.fromCharCode(bytes[j]);
					}
					onChunkRef.current(btoa(binary));
				};

				source.connect(processor);
				processor.connect(audioCtx.destination);

				if (!cancelled) setActive(true);
			} catch (err) {
				if (!cancelled) setError(err as Error);
			}
		})();

		return () => {
			cancelled = true;
			setActive(false);
			if (audioCtx) {
				void audioCtx.close().catch(() => undefined);
			}
			if (stream) {
				stream.getTracks().forEach((t) => t.stop());
			}
		};
	}, [enabled, targetSampleRate]);

	return { active, error };
}
