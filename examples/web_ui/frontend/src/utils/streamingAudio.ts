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
 * For wav, the controls element built on DATA_BLOCK_END is replay-only —
 * we intentionally skip the second autoplay there to avoid colliding with
 * the live player. ``WavStreamPlayer.finalize`` defers AudioContext
 * teardown until queued PCM has finished playing, so live audio is never
 * cut short by the end event arriving mid-playback.
 */

/** Status of a streaming audio block. */
export type StreamingAudioStatus = 'streaming' | 'ready' | 'error';

/** Per-block state surfaced to React. */
export interface StreamingAudioState {
	status: StreamingAudioStatus;
	mediaType: string;
	/** Object URL for ``<audio>`` once playback-ready. */
	url: string | null;
	/** Cause for ``status === 'error'``. */
	error?: string;
	/** Incremented each time a newer reply interrupts this block. */
	interruptCount: number;
}

type Listener = () => void;

type Bytes = Uint8Array<ArrayBufferLike>;

function base64ToBytes(b64: string): Bytes {
	// Strip whitespace, normalize URL-safe base64 (RFC 4648 §5).
	let std = b64
		.replace(/[\s\r\n]/g, '')
		.replace(/-/g, '+')
		.replace(/_/g, '/');
	const pad = std.length % 4;
	if (pad) std += '='.repeat(4 - pad);

	const binary = atob(std);
	const bytes = new Uint8Array(binary.length);
	for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
	return bytes;
}

function concatBytes(a: Bytes, b: Bytes): Bytes {
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
function parseWavHeader(bytes: Bytes): WavHeader | null {
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
		offset = chunkBody + chunkSize;
	}

	if (!sampleRate || !channels || !bitsPerSample || dataOffset < 0) return null;
	return { sampleRate, channels, bitsPerSample, dataOffset };
}

/**
 * Convert raw little-endian PCM into a Float32 AudioBuffer.
 * Supports 16-bit and 8-bit; falls back to silence for other depths.
 */
function pcmToAudioBuffer(ctx: AudioContext, pcm: Bytes, header: WavHeader): AudioBuffer | null {
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

/** Parse ``audio/pcm;rate=24000`` into a sample rate, or ``null``. */
function parsePcmMediaType(mediaType: string): number | null {
	const match = mediaType.match(/^audio\/pcm.*rate=(\d+)/);
	return match ? parseInt(match[1], 10) : null;
}

/** Live PCM playback for headerless PCM streams (e.g. DashScope realtime). */
class PcmStreamPlayer {
	private ctx: AudioContext | null = null;
	private nextStartTime = 0;
	private failed = false;
	private deferredCloseTimer: ReturnType<typeof setTimeout> | null = null;
	private readonly sampleRate: number;
	private readonly channels = 1;
	private readonly bitsPerSample = 16;
	private pending: Bytes = new Uint8Array(0);

	constructor(sampleRate: number) {
		this.sampleRate = sampleRate;
	}

	append(chunk: Bytes): void {
		if (this.failed) return;
		this.pending = concatBytes(this.pending, chunk);

		if (!this.ctx) {
			try {
				this.ctx = new AudioContext({ sampleRate: this.sampleRate });
				if (this.ctx.state === 'suspended') {
					void this.ctx.resume();
				}
				this.nextStartTime = this.ctx.currentTime;
			} catch {
				this.failed = true;
				return;
			}
		}

		const frameSize = (this.bitsPerSample / 8) * this.channels;
		const playableBytes = Math.floor(this.pending.length / frameSize) * frameSize;
		if (playableBytes === 0) return;

		const toPlay = this.pending.slice(0, playableBytes);
		this.pending = this.pending.slice(playableBytes);

		const header: WavHeader = {
			sampleRate: this.sampleRate,
			channels: this.channels,
			bitsPerSample: this.bitsPerSample,
			dataOffset: 0,
		};
		const audioBuffer = pcmToAudioBuffer(this.ctx, toPlay, header);
		if (!audioBuffer) return;

		const source = this.ctx.createBufferSource();
		source.buffer = audioBuffer;
		source.connect(this.ctx.destination);
		const startAt = Math.max(this.nextStartTime, this.ctx.currentTime);
		source.start(startAt);
		this.nextStartTime = startAt + audioBuffer.duration;
	}

	finalize(): void {
		if (!this.ctx || this.failed) {
			this.dispose();
			return;
		}
		const remainingMs = Math.max(0, this.nextStartTime - this.ctx.currentTime) * 1000;
		this.deferredCloseTimer = setTimeout(() => {
			this.dispose();
		}, remainingMs + 200);
		this.pending = new Uint8Array(0);
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
		this.pending = new Uint8Array(0);
	}
}

/**
 * Build a minimal WAV header for raw PCM data so that ``<audio>`` elements
 * can play the resulting Blob.
 */
function buildWavHeader(
	pcmByteLength: number,
	sampleRate: number,
	channels = 1,
	bitsPerSample = 16,
): Uint8Array<ArrayBuffer> {
	const headerSize = 44;
	const dataChunkSize = pcmByteLength;
	const fileSize = headerSize - 8 + dataChunkSize;
	const byteRate = sampleRate * channels * (bitsPerSample / 8);
	const blockAlign = channels * (bitsPerSample / 8);

	const buffer = new ArrayBuffer(headerSize);
	const view = new DataView(buffer);

	// RIFF header
	view.setUint32(0, 0x52494646, false); // "RIFF"
	view.setUint32(4, fileSize, true);
	view.setUint32(8, 0x57415645, false); // "WAVE"
	// fmt chunk
	view.setUint32(12, 0x666d7420, false); // "fmt "
	view.setUint32(16, 16, true); // chunk size
	view.setUint16(20, 1, true); // PCM format
	view.setUint16(22, channels, true);
	view.setUint32(24, sampleRate, true);
	view.setUint32(28, byteRate, true);
	view.setUint16(32, blockAlign, true);
	view.setUint16(34, bitsPerSample, true);
	// data chunk
	view.setUint32(36, 0x64617461, false); // "data"
	view.setUint32(40, dataChunkSize, true);

	return new Uint8Array(buffer);
}

/** Live PCM playback for an in-progress WAV stream. */
class WavStreamPlayer {
	private ctx: AudioContext | null = null;
	private header: WavHeader | null = null;
	private pending: Bytes = new Uint8Array(0);
	private nextStartTime = 0;
	private failed = false;
	private deferredCloseTimer: ReturnType<typeof setTimeout> | null = null;

	append(chunk: Bytes): void {
		if (this.failed) return;
		this.pending = concatBytes(this.pending, chunk);

		if (!this.header) {
			const header = parseWavHeader(this.pending);
			if (!header) return;
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
			this.pending = this.pending.slice(header.dataOffset);
		}

		if (!this.ctx || !this.header) return;

		const frameSize = (this.header.bitsPerSample / 8) * this.header.channels;
		const playableBytes = Math.floor(this.pending.length / frameSize) * frameSize;
		if (playableBytes === 0) return;

		const toPlay = this.pending.slice(0, playableBytes);
		this.pending = this.pending.slice(playableBytes);

		const audioBuffer = pcmToAudioBuffer(this.ctx, toPlay, this.header);
		if (!audioBuffer) return;

		const source = this.ctx.createBufferSource();
		source.buffer = audioBuffer;
		source.connect(this.ctx.destination);
		const startAt = Math.max(this.nextStartTime, this.ctx.currentTime);
		source.start(startAt);
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
		this.header = null;
		this.pending = new Uint8Array(0);
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
		this.pending = new Uint8Array(0);
		this.header = null;
	}
}

interface Session {
	mediaType: string;
	chunks: Bytes[];
	totalBytes: number;
	livePlayer: WavStreamPlayer | PcmStreamPlayer | null;
	/** True if a live player was created at start — survives dispose. */
	hadLivePlayer: boolean;
	state: StreamingAudioState;
}

/**
 * Manages the lifecycle of streaming audio DataBlocks for one chat session.
 * React components subscribe via {@link subscribe} to re-render when a block's
 * state changes.
 */
export class StreamingAudioManager {
	private sessions = new Map<string, Session>();
	private listeners = new Map<string, Set<Listener>>();

	private emit(blockId: string): void {
		this.listeners.get(blockId)?.forEach((fn) => fn());
	}

	/**
	 * Transition a streaming session to 'ready' by building a Blob URL
	 * from chunks accumulated so far. Used when playback is interrupted
	 * so the user immediately gets a play button for partial audio.
	 */
	private _promoteToReady(blockId: string, session: Session): void {
		let url: string | null = null;
		if (session.totalBytes > 0) {
			const pcmRate = parsePcmMediaType(session.mediaType);
			if (pcmRate) {
				const pcmBlob = new Blob(session.chunks as BlobPart[]);
				const wavHeader = buildWavHeader(session.totalBytes, pcmRate);
				url = URL.createObjectURL(new Blob([wavHeader, pcmBlob], { type: 'audio/wav' }));
			} else {
				url = URL.createObjectURL(
					new Blob(session.chunks as BlobPart[], { type: session.mediaType }),
				);
			}
			session.chunks = [];
		}
		session.state = {
			status: 'ready',
			mediaType: session.mediaType,
			url,
			interruptCount: session.state.interruptCount + 1,
		};
		this.emit(blockId);
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

	/** Called on DATA_BLOCK_START for an audio block. */
	start(blockId: string, mediaType: string): void {
		if (this.sessions.has(blockId)) return;
		let livePlayer: WavStreamPlayer | PcmStreamPlayer | null = null;
		if (mediaType === 'audio/wav') {
			livePlayer = new WavStreamPlayer();
		} else {
			const pcmRate = parsePcmMediaType(mediaType);
			if (pcmRate) {
				livePlayer = new PcmStreamPlayer(pcmRate);
			}
		}
		this.sessions.set(blockId, {
			mediaType,
			chunks: [],
			totalBytes: 0,
			livePlayer,
			hadLivePlayer: livePlayer !== null,
			state: { status: 'streaming', mediaType, url: null, interruptCount: 0 },
		});
		this.emit(blockId);
	}

	/** Called on DATA_BLOCK_DELTA. ``data`` is the base64 chunk payload. */
	append(blockId: string, data: string): void {
		const session = this.sessions.get(blockId);
		if (!session || !data) return;
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
	 * Called on DATA_BLOCK_END. Builds the replay URL and (for non-wav)
	 * autoplays. For wav we skip autoplay because ``WavStreamPlayer`` has
	 * already been playing the bytes as they arrived; a second ``Audio``
	 * playback here would either double up or — given ``end`` fires only
	 * after the whole model stream (text included) finishes — make the
	 * user perceive audio as starting only after text streaming completes.
	 */
	end(blockId: string): void {
		const session = this.sessions.get(blockId);
		if (!session) return;
		if (session.state.status === 'ready') return;

		const hadLivePlayback = session.hadLivePlayer;
		if (session.livePlayer) {
			// Let queued PCM finish playing; AudioContext closes via a
			// deferred timer. The livePlayer reference is kept so that
			// stopAllPlayback() can dispose() it immediately if a newer
			// reply starts before the timer fires.
			session.livePlayer.finalize();
		}

		let blob: Blob;
		const pcmRate = parsePcmMediaType(session.mediaType);
		if (pcmRate) {
			const pcmBlob = new Blob(session.chunks as BlobPart[]);
			const wavHeader = buildWavHeader(session.totalBytes, pcmRate);
			blob = new Blob([wavHeader, pcmBlob], { type: 'audio/wav' });
		} else {
			blob = new Blob(session.chunks as BlobPart[], { type: session.mediaType });
		}
		const url = URL.createObjectURL(blob);

		// Free the per-chunk buffers; the Blob owns the bytes from here on.
		session.chunks = [];

		session.state = {
			status: 'ready',
			mediaType: session.mediaType,
			url,
			interruptCount: session.state.interruptCount,
		};
		this.emit(blockId);

		if (!hadLivePlayback) {
			// Non-wav: no live playback path, so kick off autoplay now. Some
			// browsers gate this behind a user gesture; if blocked, the user
			// can still hit play on the <audio controls> element.
			const el = new Audio(url);
			void el.play().catch(() => undefined);
		}
	}

	/**
	 * Stop live streaming playback and promote interrupted sessions to
	 * 'ready' so the play button appears immediately. Called by the
	 * replay controller when clicking play on a past audio block.
	 */
	stopLivePlayback(): void {
		for (const [blockId, session] of this.sessions) {
			if (session.livePlayer) {
				session.livePlayer.dispose();
				session.livePlayer = null;
				this._promoteToReady(blockId, session);
			}
		}
	}

	/**
	 * Stop all in-progress and replay audio. Called when a new reply
	 * starts so previous audio doesn't overlap with the new one.
	 * Blob URLs are preserved so the user can still manually replay.
	 */
	stopAllPlayback(): void {
		for (const [blockId, session] of this.sessions) {
			if (session.livePlayer) {
				session.livePlayer.dispose();
				session.livePlayer = null;
			}
			if (session.state.status === 'streaming') {
				this._promoteToReady(blockId, session);
			} else {
				session.state = {
					...session.state,
					interruptCount: session.state.interruptCount + 1,
				};
				this.emit(blockId);
			}
		}
	}

	/** Release a single block's resources (Object URL etc.). */
	dispose(blockId: string): void {
		const session = this.sessions.get(blockId);
		if (!session) return;
		session.livePlayer?.dispose();
		if (session.state.url) URL.revokeObjectURL(session.state.url);
		this.sessions.delete(blockId);
		this.listeners.delete(blockId);
	}

	/** Release every block — call on unmount or session switch. */
	disposeAll(): void {
		for (const id of [...this.sessions.keys()]) this.dispose(id);
	}
}
