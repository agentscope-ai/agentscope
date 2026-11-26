/**
 * Streaming audio playback support for assistant DataBlocks.
 *
 * Omni-style models (e.g. qwen3.5-omni-plus) deliver speech as a stream of
 * DATA_BLOCK_DELTA events carrying base64 audio bytes for one logical
 * DataBlock. This module routes those chunks to two playback paths:
 *
 *   1. **Live chunked playback** — only for audio/wav. We parse the RIFF
 *      header from the first chunk and schedule subsequent PCM bytes onto a
 *      Web Audio timeline as they arrive, so the assistant starts speaking
 *      the moment the first chunk arrives — without waiting for
 *      DATA_BLOCK_END, which the agent only emits after the *entire* model
 *      stream (including text) finishes.
 *
 *   2. **Buffered final playback** — for non-wav formats (mp3/opus/etc.) we
 *      accumulate bytes and, on DATA_BLOCK_END, build a Blob URL that backs
 *      autoplay + an ``<audio controls>`` element for replay/scrubbing.
 *
 * Once live WAV playback starts, the controls element is replay-only.
 * A checkpoint can supply the full WAV before the first playable frame.
 * ``WavStreamPlayer.finalize`` defers AudioContext
 * teardown until queued PCM has finished playing, so live audio is never
 * cut short by the end event arriving mid-playback.
 */

import { type DataBlockDeltaEvent, ReplyFinishedReason } from '@agentscope-ai/agentscope/event';
import type { Msg } from '@agentscope-ai/agentscope/message';

/** Status of a streaming audio block. */
export type StreamingAudioStatus = 'streaming' | 'ready' | 'error';

/** Per-block state surfaced to React. */
export interface StreamingAudioState {
	status: StreamingAudioStatus;
	mediaType: string;
	/** Playback URL for ``<audio>`` once playback-ready. */
	url: string | null;
	/** Cause for ``status === 'error'``. */
	error?: string;
	/** Incremented to pause the block's replay control. */
	interruptCount: number;
}

type Listener = () => void;

type Bytes = Uint8Array<ArrayBufferLike>;

const decodedChunks = new WeakMap<DataBlockDeltaEvent, Uint8Array<ArrayBuffer>>();

/** Both players consume the same SSE event, including when ownership is resolved later. */
export function base64ToBytes(chunk: string | DataBlockDeltaEvent): Uint8Array<ArrayBuffer> {
	if (typeof chunk !== 'string') {
		const cached = decodedChunks.get(chunk);
		if (cached) return cached;
	}
	const binary = atob(typeof chunk === 'string' ? chunk : chunk.data);
	const bytes = new Uint8Array(binary.length);
	for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
	if (typeof chunk !== 'string') decodedChunks.set(chunk, bytes);
	return bytes;
}

function concatBytes(a: Bytes, b: Bytes): Uint8Array<ArrayBuffer> {
	const out = new Uint8Array(a.length + b.length);
	out.set(a, 0);
	out.set(b, a.length);
	return out;
}

interface WavHeader {
	sampleRate: number;
	channels: number;
	bitsPerSample: number;
	/** Byte offset where PCM samples begin (right after the ``data`` chunk header). */
	dataOffset: number;
}

/**
 * Parse a minimal RIFF/WAVE header. Returns ``null`` if the buffer doesn't
 * yet contain enough bytes to locate the ``fmt `` and ``data`` chunks.
 */
export function parseWavHeader(bytes: Bytes): WavHeader | null {
	if (bytes.length < 44) return null;
	const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
	// "RIFF"...."WAVE"
	if (view.getUint32(0, false) !== 0x52494646 || view.getUint32(8, false) !== 0x57415645) {
		return null;
	}

	let offset = 12;
	let sampleRate = 0;
	let channels = 0;
	let bitsPerSample = 0;
	let dataOffset = -1;

	while (offset + 8 <= bytes.length) {
		const chunkId = view.getUint32(offset, false);
		const chunkSize = view.getUint32(offset + 4, true);
		const chunkBody = offset + 8;

		if (chunkId === 0x666d7420 /* "fmt " */) {
			if (chunkBody + 16 > bytes.length) return null;
			channels = view.getUint16(chunkBody + 2, true);
			sampleRate = view.getUint32(chunkBody + 4, true);
			bitsPerSample = view.getUint16(chunkBody + 14, true);
		} else if (chunkId === 0x64617461 /* "data" */) {
			dataOffset = chunkBody;
			break;
		}
		// RIFF pads odd-sized chunks to the next even byte boundary.
		offset = chunkBody + chunkSize + (chunkSize % 2);
	}

	if (!sampleRate || !channels || !bitsPerSample || dataOffset < 0) return null;
	return { sampleRate, channels, bitsPerSample, dataOffset };
}

/**
 * Convert raw little-endian PCM into a Float32 AudioBuffer.
 * Supports 16-bit and 8-bit; falls back to silence for other depths.
 */
export function pcmToAudioBuffer(
	ctx: AudioContext,
	pcm: Bytes,
	header: WavHeader,
): AudioBuffer | null {
	const { channels, bitsPerSample, sampleRate } = header;
	const bytesPerSample = bitsPerSample / 8;
	const frameSize = bytesPerSample * channels;
	const frameCount = Math.floor(pcm.length / frameSize);
	if (frameCount === 0) return null;

	const buffer = ctx.createBuffer(channels, frameCount, sampleRate);
	const view = new DataView(pcm.buffer, pcm.byteOffset, pcm.byteLength);

	for (let ch = 0; ch < channels; ch++) {
		const channelData = buffer.getChannelData(ch);
		for (let i = 0; i < frameCount; i++) {
			const sampleOffset = i * frameSize + ch * bytesPerSample;
			if (bitsPerSample === 16) {
				channelData[i] = view.getInt16(sampleOffset, true) / 0x8000;
			} else if (bitsPerSample === 8) {
				channelData[i] = (view.getUint8(sampleOffset) - 128) / 128;
			} else {
				channelData[i] = 0;
			}
		}
	}
	return buffer;
}

/** Live PCM playback for an in-progress WAV stream. */
class WavStreamPlayer {
	private frameCount = 0;
	private readonly resumeFrames: number;
	started: boolean;
	private ctx: AudioContext | null = null;
	private header: WavHeader | null = null;
	private pending: Bytes | null = null;
	private nextStartTime = 0;
	private failed = false;
	private deferredCloseTimer: ReturnType<typeof setTimeout> | null = null;

	constructor(resumeFrames = 0) {
		this.resumeFrames = resumeFrames;
		this.started = resumeFrames > 0;
	}

	/** Frames heard before this context is closed, excluding scheduled audio. */
	get playedFrames(): number {
		const remaining =
			this.ctx && this.header
				? Math.max(0, this.nextStartTime - this.ctx.currentTime) * this.header.sampleRate
				: 0;
		return Math.max(this.resumeFrames, this.frameCount - Math.ceil(remaining));
	}

	append(chunk: Bytes): void {
		if (this.failed) return;
		let bytes = this.pending ? concatBytes(this.pending, chunk) : chunk;

		if (!this.header) {
			const header = parseWavHeader(bytes);
			if (!header) {
				this.pending = bytes;
				return;
			}
			this.header = header;
			try {
				this.ctx = new AudioContext({ sampleRate: header.sampleRate });
				if (this.ctx.state === 'suspended') {
					void this.ctx.resume();
				}
				this.nextStartTime = this.ctx.currentTime;
			} catch {
				this.failed = true;
				return;
			}
			bytes = bytes.subarray(header.dataOffset);
		}

		if (!this.ctx || !this.header) return;

		const frameSize = (this.header.bitsPerSample / 8) * this.header.channels;
		const playableBytes = Math.floor(bytes.length / frameSize) * frameSize;
		// Keep only the incomplete frame without retaining the whole chunk.
		this.pending = playableBytes < bytes.length ? bytes.slice(playableBytes) : null;
		if (playableBytes === 0) return;

		const skip = Math.max(0, this.resumeFrames - this.frameCount) * frameSize;
		this.frameCount += playableBytes / frameSize;
		if (skip >= playableBytes) return;
		const toPlay = bytes.subarray(skip, playableBytes);
		const audioBuffer = pcmToAudioBuffer(this.ctx, toPlay, this.header);
		if (!audioBuffer) return;

		const source = this.ctx.createBufferSource();
		source.buffer = audioBuffer;
		source.connect(this.ctx.destination);
		const startAt = Math.max(this.nextStartTime, this.ctx.currentTime);
		source.start(startAt);
		this.started = true;
		this.nextStartTime = startAt + audioBuffer.duration;
	}

	/**
	 * Graceful end-of-stream: stop accepting more bytes, then close the
	 * AudioContext only after the last queued buffer has finished playing.
	 * Without this, ``end()`` calling ``dispose()`` would cut live playback
	 * short by closing the context while sources are still scheduled.
	 *
	 * The AudioContext reference is kept on ``this.ctx`` so that
	 * ``dispose()`` can still close it immediately if a newer reply
	 * interrupts playback before the timer fires.
	 */
	finalize(): void {
		if (!this.ctx || this.failed) {
			this.dispose();
			return;
		}
		const remainingMs = Math.max(0, this.nextStartTime - this.ctx.currentTime) * 1000;
		this.deferredCloseTimer = setTimeout(() => {
			this.dispose();
		}, remainingMs + 200);
		this.pending = null;
	}

	dispose(): void {
		if (this.deferredCloseTimer) {
			clearTimeout(this.deferredCloseTimer);
			this.deferredCloseTimer = null;
		}
		if (this.ctx) {
			void this.ctx.close().catch(() => undefined);
			this.ctx = null;
		}
		this.pending = null;
		this.header = null;
	}
}

interface Session {
	replyId?: string;
	sourceUrl?: string;
	mediaType: string;
	autoplay: boolean | Promise<boolean>;
	chunks: Bytes[];
	totalBytes: number;
	livePlayer: WavStreamPlayer | null;
	playedUrl: boolean;
	autoplayElement: HTMLAudioElement | null;
	state: StreamingAudioState;
}

/** Resume only when the checkpoint starts with the exact bytes already fed to the player. */
function resumeWav(session: Session, player: WavStreamPlayer, data: string): boolean {
	let bytes: Uint8Array<ArrayBuffer>;
	try {
		bytes = base64ToBytes(data);
	} catch {
		return false;
	}
	let offset = 0;
	for (const chunk of session.chunks) {
		if (!chunk.every((byte, i) => byte === bytes[offset + i])) return false;
		offset += chunk.length;
	}
	if (offset < bytes.length) player.append(bytes.subarray(offset));
	session.chunks = [bytes];
	session.totalBytes = bytes.length;
	return true;
}

/**
 * Manages the lifecycle of streaming audio DataBlocks for one chat session.
 * React components subscribe via {@link subscribe} to re-render when a block's
 * state changes.
 */
export class StreamingAudioManager {
	private sessions = new Map<string, Session>();
	private replay = new Map<
		string,
		{ replyId?: string; mediaType: string; frames: number | false }
	>();
	private listeners = new Map<string, Set<Listener>>();

	private emit(blockId: string): void {
		this.listeners.get(blockId)?.forEach((fn) => fn());
	}

	subscribe(blockId: string, fn: Listener): () => void {
		let set = this.listeners.get(blockId);
		if (!set) {
			set = new Set();
			this.listeners.set(blockId, set);
		}
		set.add(fn);
		return () => {
			set?.delete(fn);
			if (set && set.size === 0) this.listeners.delete(blockId);
		};
	}

	getState(blockId: string): StreamingAudioState | null {
		return this.sessions.get(blockId)?.state ?? null;
	}

	/** Keep completed checkpoint audio playing and rebuild other blocks from replay. */
	restoreCheckpoint(messages: Msg[]): void {
		if (!this.sessions.size && !this.replay.size) return;
		const replayed = new Set(this.sessions.keys());
		for (const msg of messages) {
			for (const block of msg.content) {
				if (block.type !== 'data' || !block.finished_at) continue;
				// A second reconnect may checkpoint the WAV before its replay starts.
				const resume = this.replay.get(block.id);
				if (resume) {
					if (
						typeof resume.frames === 'number' &&
						resume.replyId === msg.id &&
						resume.mediaType === block.source.media_type &&
						block.source.type === 'base64' &&
						(!msg.finished_reason ||
							msg.finished_reason === ReplyFinishedReason.COMPLETED)
					) {
						this.start(block.id, resume.mediaType, true, msg.id);
					}
					this.replay.delete(block.id);
				}
				replayed.delete(block.id);
				const session = this.sessions.get(block.id);
				if (!session) continue;
				if (
					session.replyId !== msg.id ||
					session.mediaType !== block.source.media_type ||
					(msg.finished_reason && msg.finished_reason !== ReplyFinishedReason.COMPLETED)
				) {
					this.dispose(block.id);
					continue;
				}
				const { source } = block;
				const url = source.type === 'url' ? source.url : undefined;
				if (session.state.status === 'streaming' && session.autoplay !== false) {
					if (session.livePlayer?.started) {
						if (
							source.type !== 'base64' ||
							!resumeWav(session, session.livePlayer, source.data)
						) {
							this.dispose(block.id);
							continue;
						}
					} else {
						session.livePlayer?.dispose();
						session.livePlayer = null;
						session.chunks = [];
						session.totalBytes = 0;
						session.sourceUrl = undefined;
						this.append(block.id, source.type === 'base64' ? source.data : null, url);
					}
					void this.end(block.id);
				} else if (session.state.status !== 'ready' || session.sourceUrl !== url) {
					this.dispose(block.id);
				}
			}
		}
		for (const id of replayed) {
			const session = this.sessions.get(id)!;
			if (session.autoplay !== true || session.playedUrl || session.livePlayer?.started) {
				this.replay.set(id, {
					replyId: session.replyId,
					mediaType: session.mediaType,
					frames:
						session.autoplay === true && session.livePlayer?.started
							? session.livePlayer.playedFrames
							: false,
				});
			}
			this.dispose(id);
		}
	}

	/** Track an audio block; disable autoplay when another player owns output. */
	start(
		blockId: string,
		mediaType: string,
		autoplay: boolean | Promise<boolean> = true,
		replyId?: string,
	): void {
		if (typeof autoplay !== 'boolean') autoplay = autoplay.catch(() => false);
		if (this.sessions.has(blockId)) return;
		const resume = this.replay.get(blockId);
		this.replay.delete(blockId);
		const frames =
			resume?.replyId === replyId && resume?.mediaType === mediaType ? resume.frames : 0;
		if (frames === false) autoplay = false;
		const livePlayer =
			autoplay === true && mediaType === 'audio/wav'
				? new WavStreamPlayer(frames || 0)
				: null;
		this.sessions.set(blockId, {
			replyId,
			mediaType,
			autoplay,
			chunks: [],
			totalBytes: 0,
			livePlayer,
			playedUrl: false,
			autoplayElement: null,
			state: { status: 'streaming', mediaType, url: null, interruptCount: 0 },
		});
		if (typeof autoplay !== 'boolean') {
			void autoplay.then((allowed) => {
				const session = this.sessions.get(blockId);
				if (!session || session.autoplay !== autoplay) return;
				session.autoplay = allowed;
				if (!allowed) return;
				if (session.state.url) {
					this.playUrl(session, session.state.url);
				} else if (mediaType === 'audio/wav' && session.state.status === 'streaming') {
					session.livePlayer = new WavStreamPlayer(frames || 0);
					for (const chunk of session.chunks) session.livePlayer.append(chunk);
				}
			});
		}
		this.emit(blockId);
	}

	/** A delta carries either base64 audio bytes or a playable URL. */
	append(blockId: string, data: string | DataBlockDeltaEvent | null, url?: string): void {
		const session = this.sessions.get(blockId);
		if (!session || session.state.status !== 'streaming') return;
		if (url) {
			session.sourceUrl = url;
			return;
		}
		if (!data) return;
		let bytes: Uint8Array;
		try {
			bytes = base64ToBytes(data);
		} catch {
			return;
		}
		session.chunks.push(bytes);
		session.totalBytes += bytes.length;
		session.livePlayer?.append(bytes);
		// No emit — partial bytes don't change the rendered UI yet.
	}

	/**
	 * Build the replay URL on DATA_BLOCK_END. Autoplay the completed block
	 * when the live player hasn't scheduled any audio frames.
	 */
	async end(blockId: string, playbackDone?: Promise<void>): Promise<void> {
		const session = this.sessions.get(blockId);
		if (playbackDone) {
			try {
				await playbackDone;
			} catch {
				if (session) session.autoplay = false;
			}
			if (this.sessions.get(blockId) !== session) return;
		}
		if (!session || session.state.status !== 'streaming') return;

		const hadLivePlayback = session.livePlayer?.started;
		if (session.livePlayer) {
			// Let queued PCM finish playing; AudioContext closes via a
			// deferred timer. The livePlayer reference is kept so that
			// stopAllPlayback() can dispose() it immediately if a newer
			// reply starts before the timer fires.
			session.livePlayer.finalize();
		}

		let url = session.sourceUrl ?? null;
		if (!url && session.totalBytes) {
			const blob = new Blob(session.chunks as BlobPart[], { type: session.mediaType });
			url = URL.createObjectURL(blob);
		}

		// Free the per-chunk buffers; the Blob owns the bytes from here on.
		session.chunks = [];

		session.state = {
			status: url ? 'ready' : 'error',
			mediaType: session.mediaType,
			url,
			interruptCount: session.state.interruptCount,
		};
		this.emit(blockId);

		if (url && session.autoplay === true && !hadLivePlayback) {
			this.playUrl(session, url);
		}
	}

	private playUrl(session: Session, url: string): void {
		const el = new Audio(url);
		session.autoplayElement = el;
		void el.play().then(
			() => {
				session.playedUrl = true;
			},
			() => undefined,
		);
	}

	/** Stop this reply's playback and finalize any unfinished blocks for replay. */
	interruptReply(replyId: string): void {
		for (const resume of this.replay.values()) {
			if (resume.replyId === replyId) resume.frames = false;
		}
		for (const [blockId, session] of this.sessions) {
			if (session.replyId !== replyId) continue;
			this.stopBlockPlayback(session);
			if (session.state.status === 'streaming') {
				if (session.totalBytes || session.sourceUrl) {
					void this.end(blockId);
					continue;
				}
				session.state = { ...session.state, status: 'error' };
			}
			this.emit(blockId);
		}
	}

	private stopBlockPlayback(session: Session, interruptReplay = true): void {
		session.autoplay = false;
		session.autoplayElement?.pause();
		session.autoplayElement = null;
		session.livePlayer?.dispose();
		session.livePlayer = null;
		if (interruptReplay) {
			session.state = { ...session.state, interruptCount: session.state.interruptCount + 1 };
		}
	}

	/** Give manual replay control of audio output, including buffered MP3 playback. */
	stopAutoplay(): void {
		for (const resume of this.replay.values()) resume.frames = false;
		for (const session of this.sessions.values()) this.stopBlockPlayback(session, false);
	}

	/**
	 * Stop all in-progress and replay audio. Called when a new reply
	 * starts so previous audio doesn't overlap with the new one.
	 * Blob URLs are preserved so the user can still manually replay.
	 */
	stopAllPlayback(preserveReplayPositions = false): void {
		// Replayed reply starts can precede the WAV's own start in the log.
		if (!preserveReplayPositions) {
			for (const resume of this.replay.values()) resume.frames = false;
		}
		for (const [blockId, session] of this.sessions) {
			this.stopBlockPlayback(session);
			this.emit(blockId);
		}
	}

	/** Release a single block's resources (Object URL etc.). */
	dispose(blockId: string): void {
		const session = this.sessions.get(blockId);
		if (!session) return;
		session.autoplayElement?.pause();
		session.livePlayer?.dispose();
		if (session.state.url && !session.sourceUrl) URL.revokeObjectURL(session.state.url);
		this.sessions.delete(blockId);
		this.listeners.delete(blockId);
	}

	/** Release every block — call on unmount or session switch. */
	disposeAll(): void {
		this.replay.clear();
		for (const id of [...this.sessions.keys()]) this.dispose(id);
	}
}
