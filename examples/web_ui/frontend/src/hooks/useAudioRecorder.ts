import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

export type AudioRecorderState = 'idle' | 'requesting' | 'recording' | 'transcribing';

interface UseAudioRecorderOptions {
	acceptedTypes: string[];
	resetKey: string;
	onRecorded: (file: File) => Promise<void>;
	onError: (error: unknown) => void;
}

const MIME_CANDIDATES: Record<string, string[]> = {
	'audio/webm': ['audio/webm;codecs=opus', 'audio/webm'],
	'audio/ogg': ['audio/ogg;codecs=opus', 'audio/ogg'],
	'audio/mp4': ['audio/mp4'],
	'audio/mpeg': ['audio/mpeg'],
	'audio/wav': ['audio/wav'],
};

const FILE_EXTENSIONS: Record<string, string> = {
	'audio/webm': 'webm',
	'audio/ogg': 'ogg',
	'audio/mp4': 'mp4',
	'audio/mpeg': 'mp3',
	'audio/wav': 'wav',
};

/** Pick a MediaRecorder format accepted by both the browser and ASR model. */
function selectMimeType(acceptedTypes: string[]): string | null {
	if (typeof MediaRecorder === 'undefined') return null;
	for (const [baseType, candidates] of Object.entries(MIME_CANDIDATES)) {
		if (!acceptedTypes.includes(baseType) && !acceptedTypes.includes('audio/*')) {
			continue;
		}
		for (const candidate of candidates) {
			if (MediaRecorder.isTypeSupported(candidate)) return candidate;
		}
	}
	return null;
}

/** Record one microphone clip and hand it to an asynchronous consumer. */
export function useAudioRecorder({
	acceptedTypes,
	resetKey,
	onRecorded,
	onError,
}: UseAudioRecorderOptions) {
	const [state, setState] = useState<AudioRecorderState>('idle');
	const recorderRef = useRef<MediaRecorder | null>(null);
	const streamRef = useRef<MediaStream | null>(null);
	const chunksRef = useRef<Blob[]>([]);
	const generationRef = useRef(0);
	const onRecordedRef = useRef(onRecorded);
	const onErrorRef = useRef(onError);

	useEffect(() => {
		onRecordedRef.current = onRecorded;
		onErrorRef.current = onError;
	}, [onRecorded, onError]);

	const mimeType = useMemo(() => selectMimeType(acceptedTypes), [acceptedTypes]);
	const supported =
		typeof navigator !== 'undefined' &&
		!!navigator.mediaDevices?.getUserMedia &&
		mimeType !== null;

	const stopTracks = useCallback(() => {
		streamRef.current?.getTracks().forEach((track) => track.stop());
		streamRef.current = null;
	}, []);

	const reset = useCallback(() => {
		generationRef.current += 1;
		const recorder = recorderRef.current;
		recorderRef.current = null;
		if (recorder && recorder.state !== 'inactive') {
			recorder.onstop = null;
			recorder.stop();
		}
		chunksRef.current = [];
		stopTracks();
		setState('idle');
	}, [stopTracks]);

	useEffect(() => {
		reset();
		return reset;
	}, [mimeType, reset, resetKey]);

	const start = useCallback(async () => {
		if (!supported || !mimeType || state !== 'idle') return;
		const generation = ++generationRef.current;
		setState('requesting');
		try {
			const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
			if (generation !== generationRef.current) {
				stream.getTracks().forEach((track) => track.stop());
				return;
			}

			streamRef.current = stream;
			chunksRef.current = [];
			const recorder = new MediaRecorder(stream, { mimeType });
			recorderRef.current = recorder;
			recorder.ondataavailable = (event) => {
				if (event.data.size > 0) chunksRef.current.push(event.data);
			};
			recorder.onstop = () => {
				const chunks = chunksRef.current;
				chunksRef.current = [];
				recorderRef.current = null;
				stopTracks();

				if (generation !== generationRef.current || chunks.length === 0) {
					setState('idle');
					return;
				}

				const baseType = recorder.mimeType.split(';')[0];
				const extension = FILE_EXTENSIONS[baseType] ?? 'webm';
				const file = new File(chunks, `recording.${extension}`, {
					type: recorder.mimeType,
				});
				setState('transcribing');
				void onRecordedRef
					.current(file)
					.catch((error) => {
						if (generation === generationRef.current) {
							onErrorRef.current(error);
						}
					})
					.finally(() => {
						if (generation === generationRef.current) setState('idle');
					});
			};
			recorder.start();
			setState('recording');
		} catch (error) {
			if (generation === generationRef.current) {
				stopTracks();
				setState('idle');
				onErrorRef.current(error);
			}
		}
	}, [mimeType, state, stopTracks, supported]);

	const stop = useCallback(() => {
		if (state !== 'recording') return;
		const recorder = recorderRef.current;
		if (recorder?.state === 'recording') recorder.stop();
	}, [state]);

	const toggle = useCallback(() => {
		if (state === 'recording') {
			stop();
			return;
		}
		if (state === 'idle') void start();
	}, [start, state, stop]);

	return { state, supported, toggle };
}
