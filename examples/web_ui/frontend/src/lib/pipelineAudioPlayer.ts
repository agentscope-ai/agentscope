import { parseWavHeader, pcmToAudioBuffer } from '@/utils/streamingAudio';

interface AudioBlock {
	id: string;
	mediaType: string;
	bytes: Uint8Array<ArrayBuffer>;
	header: ReturnType<typeof parseWavHeader>;
	playbackDone?: Promise<void>;
	finishPlayback?: () => void;
	decoding: boolean;
}

/** Serial speech playback with completion tied to the actual audio sources. */
export class PipelineAudioPlayer {
	private blocks: AudioBlock[] = [];
	private sources = new Set<AudioBufferSourceNode>();
	private nextTime = 0;
	private generation = 0;
	private readonly context: AudioContext;
	private readonly onChange: (busy: boolean) => void;
	private readonly onError: (error: unknown) => void;

	constructor(
		context: AudioContext,
		onChange: (busy: boolean) => void,
		onError: (error: unknown) => void,
	) {
		this.context = context;
		this.onChange = onChange;
		this.onError = onError;
	}

	get busy(): boolean {
		return this.blocks.length > 0;
	}

	start(id: string, mediaType: string): void {
		if (mediaType.split(';', 1)[0].trim().toLowerCase() === 'audio/pcm') {
			this.onError(
				new Error(
					'Raw PCM is not supported in pipeline voice. Select WAV or MP3 in TTS settings.',
				),
			);
			return;
		}
		if (this.blocks.some((block) => block.id === id)) return;
		this.blocks.push({
			id,
			mediaType,
			bytes: new Uint8Array(),
			header: null,
			decoding: false,
		});
		this.onChange(true);
	}

	append(id: string, data: string): void {
		const block = this.blocks.find((item) => item.id === id);
		if (!block || block.playbackDone) return;
		try {
			const decoded = atob(data);
			const bytes = new Uint8Array(block.bytes.length + decoded.length);
			bytes.set(block.bytes);
			for (let i = 0; i < decoded.length; i++)
				bytes[block.bytes.length + i] = decoded.charCodeAt(i);
			block.bytes = bytes;
			this.pump();
		} catch (error) {
			this.onError(error);
		}
	}

	/** Resolve after this block's last source ends or playback is stopped. */
	end(id: string): Promise<void> | undefined {
		const block = this.blocks.find((item) => item.id === id);
		if (!block) return;
		block.playbackDone ??= new Promise<void>((resolve) => {
			block.finishPlayback = resolve;
		});
		this.pump();
		return block.playbackDone;
	}

	stop(): void {
		this.generation++;
		for (const source of this.sources) {
			source.onended = null;
			source.stop();
			source.disconnect();
		}
		this.sources.clear();
		for (const block of this.blocks) block.finishPlayback?.();
		this.blocks = [];
		this.nextTime = 0;
		this.onChange(false);
	}

	private play(buffer: AudioBuffer): void {
		if (this.context.state !== 'running') throw new Error('Audio playback is suspended.');
		const source = this.context.createBufferSource();
		source.buffer = buffer;
		source.connect(this.context.destination);
		this.sources.add(source);
		source.onended = () => {
			this.sources.delete(source);
			source.disconnect();
			this.pump();
		};
		const startAt = Math.max(this.context.currentTime, this.nextTime);
		source.start(startAt);
		this.nextTime = startAt + buffer.duration;
	}

	private pump(): void {
		const block = this.blocks[0];
		if (!block || block.decoding) return;
		try {
			if (block.mediaType === 'audio/wav') {
				if (!block.header) {
					block.header = parseWavHeader(block.bytes);
					if (!block.header) {
						if (block.playbackDone) throw new Error('Invalid WAV audio.');
						return;
					}
					if (![8, 16].includes(block.header.bitsPerSample))
						throw new Error('Unsupported PCM depth.');
					block.bytes = block.bytes.slice(block.header.dataOffset);
				}
				const frameSize = (block.header.channels * block.header.bitsPerSample) / 8;
				const count = Math.floor(block.bytes.length / frameSize) * frameSize;
				if (count) {
					const buffer = pcmToAudioBuffer(
						this.context,
						block.bytes.subarray(0, count),
						block.header,
					);
					block.bytes = block.bytes.slice(count);
					if (buffer) this.play(buffer);
				}
			} else if (block.playbackDone && block.bytes.length) {
				block.decoding = true;
				const generation = this.generation;
				void this.context
					.decodeAudioData(block.bytes.buffer)
					.then((buffer) => {
						if (generation !== this.generation) return;
						block.bytes = new Uint8Array();
						block.decoding = false;
						this.play(buffer);
					})
					.catch((error) => {
						if (generation === this.generation) this.onError(error);
					});
				return;
			}
			if (block.playbackDone && this.sources.size === 0) {
				this.blocks.shift();
				block.finishPlayback?.();
				this.onChange(this.busy);
				this.pump();
			}
		} catch (error) {
			this.onError(error);
		}
	}
}
