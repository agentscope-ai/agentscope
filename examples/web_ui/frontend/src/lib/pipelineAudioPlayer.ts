import type { DataBlockDeltaEvent } from '@agentscope-ai/agentscope/event';

import { base64ToBytes, parseWavHeader, pcmToAudioBuffer } from '@/utils/streamingAudio';

interface AudioBlock {
	id: string;
	mediaType: string;
	chunks: Uint8Array<ArrayBuffer>[];
	header: ReturnType<typeof parseWavHeader>;
	pcmBytesPlayed: number;
	playbackDone?: Promise<void>;
	finishPlayback?: () => void;
	decoding: boolean;
}

/** Consume queued chunks into a buffer owned by this player. */
function joinChunks(block: AudioBlock): Uint8Array<ArrayBuffer> {
	const bytes = new Uint8Array(block.chunks.reduce((size, chunk) => size + chunk.length, 0));
	let offset = 0;
	for (const chunk of block.chunks) {
		bytes.set(chunk, offset);
		offset += chunk.length;
	}
	block.chunks.length = 0;
	return bytes;
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
			chunks: [],
			header: null,
			pcmBytesPlayed: 0,
			decoding: false,
		});
		this.onChange(true);
	}

	append(id: string, data: string | DataBlockDeltaEvent): void {
		const block = this.blocks.find((item) => item.id === id);
		if (!block || block.playbackDone) return;
		try {
			const chunk = base64ToBytes(data);
			if (!chunk.length) return;
			block.chunks.push(chunk);
			if (block === this.blocks[0]) this.pump();
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
		if (block === this.blocks[0]) this.pump();
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
				let bytes = block.chunks.length === 1 ? block.chunks.pop()! : joinChunks(block);
				if (!block.header) {
					block.header = parseWavHeader(bytes);
					if (!block.header) {
						if (block.playbackDone) throw new Error('Invalid WAV audio.');
						if (bytes.length) block.chunks.push(bytes);
						return;
					}
					if (![8, 16].includes(block.header.bitsPerSample))
						throw new Error('Unsupported PCM depth.');
					bytes = bytes.subarray(block.header.dataOffset);
				}
				const frameSize = (block.header.channels * block.header.bitsPerSample) / 8;
				bytes = bytes.subarray(0, Math.max(0, block.header.dataSize - block.pcmBytesPlayed));
				const count = bytes.length - (bytes.length % frameSize);
				block.pcmBytesPlayed += count;
				if (count < bytes.length) block.chunks.push(bytes.slice(count));
				if (count) {
					const buffer = pcmToAudioBuffer(
						this.context,
						bytes.subarray(0, count),
						block.header,
					);
					if (buffer) this.play(buffer);
				}
			} else if (block.playbackDone && block.chunks.length) {
				block.decoding = true;
				const generation = this.generation;
				// decodeAudioData detaches its input. The replay buffer still owns its chunks.
				const bytes = joinChunks(block);
				void this.context
					.decodeAudioData(bytes.buffer)
					.then((buffer) => {
						if (generation !== this.generation) return;
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
