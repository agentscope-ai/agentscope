import type { UserConfirmResultEvent } from '@agentscope-ai/agentscope/event';

import { realtimeApi } from '@/api';

export type RealtimeConnectionState = 'idle' | 'connecting' | 'active' | 'error';

interface BrowserWebRTCTransportOptions {
	agentId: string;
	sessionId: string;
	onStateChange: (state: RealtimeConnectionState) => void;
	onError: (error: Error) => void;
}

interface AudioStartFrame {
	type: 'audio_start';
	item_id: string;
	track_time_ms: number;
}

interface AudioDurationFrame {
	type: 'audio_duration';
	item_id: string;
	duration_ms: number;
}

interface ClearAudioFrame {
	type: 'clear_audio';
	request_id: string;
	resume_track_time_ms: number;
}

interface ErrorFrame {
	type: 'error';
	detail: string;
}

type ServerFrame = AudioStartFrame | AudioDurationFrame | ClearAudioFrame | ErrorFrame;

const ICE_GATHERING_TIMEOUT_MS = 10_000;
const CONNECTION_TIMEOUT_MS = 20_000;

/** WebRTC microphone, model audio, and DataChannel control transport. */
export class BrowserWebRTCTransport {
	private readonly options: BrowserWebRTCTransportOptions;
	private peerConnection: RTCPeerConnection | null = null;
	private controlChannel: RTCDataChannel | null = null;
	private localStream: MediaStream | null = null;
	private remoteStream: MediaStream | null = null;
	private audioElement: HTMLAudioElement | null = null;
	private progressTimer: number | null = null;
	private playoutFenceTimer: number | null = null;
	private itemId = '';
	private itemTrackStartMs = 0;
	private readonly durationsMs = new Map<string, number>();
	private intentionalClose = false;
	private failed = false;
	private active = false;
	private failureError: Error | null = null;

	constructor(options: BrowserWebRTCTransportOptions) {
		this.options = options;
	}

	async start(): Promise<void> {
		this.intentionalClose = false;
		this.failed = false;
		this.active = false;
		this.failureError = null;
		this.options.onStateChange('connecting');
		if (!navigator.mediaDevices?.getUserMedia || !window.RTCPeerConnection) {
			throw new Error('WebRTC microphone capture is not available in this browser.');
		}

		const { ice_servers: iceServers } = await realtimeApi.config();
		this.throwIfStopped();
		const localStream = await navigator.mediaDevices.getUserMedia({
			audio: {
				channelCount: 1,
				echoCancellation: true,
				noiseSuppression: true,
				autoGainControl: true,
			},
		});
		this.localStream = localStream;
		this.throwIfStopped();

		const peerConnection = new RTCPeerConnection({ iceServers });
		this.peerConnection = peerConnection;
		const controlChannel = peerConnection.createDataChannel('control', {
			ordered: true,
		});
		this.controlChannel = controlChannel;
		controlChannel.onmessage = (event) => this.handleMessage(event.data);
		controlChannel.onclose = () => {
			if (!this.intentionalClose) {
				this.fail(new Error('The realtime control channel was closed.'));
			}
		};

		peerConnection.ontrack = (event) => this.attachRemoteTrack(event.track);
		peerConnection.onconnectionstatechange = () => {
			if (
				!this.intentionalClose &&
				(peerConnection.connectionState === 'failed' ||
					peerConnection.connectionState === 'closed')
			) {
				this.fail(new Error('The realtime media connection was lost.'));
			}
		};
		for (const track of localStream.getTracks()) {
			peerConnection.addTrack(track, localStream);
		}

		const channelReady = this.waitForControlChannel(controlChannel);
		void channelReady.catch(() => undefined);
		const offer = await peerConnection.createOffer();
		this.throwIfStopped();
		await peerConnection.setLocalDescription(offer);
		await this.waitForIceGathering(peerConnection);
		this.throwIfStopped();
		const localDescription = peerConnection.localDescription;
		if (!localDescription || localDescription.type !== 'offer') {
			throw new Error('The browser did not produce a WebRTC offer.');
		}

		const answer = await realtimeApi.offer(this.options.sessionId, {
			agent_id: this.options.agentId,
			sdp: localDescription.sdp,
			type: 'offer',
		});
		this.throwIfStopped();
		await peerConnection.setRemoteDescription({
			sdp: answer.sdp,
			type: answer.type,
		});
		await channelReady;
		this.throwIfStopped();
		if (this.failureError) throw this.failureError;

		this.progressTimer = window.setInterval(() => this.reportPlayout(), 100);
		this.active = true;
		this.options.onStateChange('active');
	}

	async stop(): Promise<void> {
		this.intentionalClose = true;
		if (this.progressTimer !== null) {
			window.clearInterval(this.progressTimer);
			this.progressTimer = null;
		}
		if (this.playoutFenceTimer !== null) {
			window.clearTimeout(this.playoutFenceTimer);
			this.playoutFenceTimer = null;
		}
		this.sendJson({ type: 'close' });
		this.controlChannel?.close();
		this.peerConnection?.close();
		this.localStream?.getTracks().forEach((track) => track.stop());
		this.remoteStream?.getTracks().forEach((track) => track.stop());
		if (this.audioElement) {
			this.audioElement.pause();
			this.audioElement.srcObject = null;
		}
		this.controlChannel = null;
		this.peerConnection = null;
		this.localStream = null;
		this.remoteStream = null;
		this.audioElement = null;
		this.itemId = '';
		this.itemTrackStartMs = 0;
		this.durationsMs.clear();
		this.active = false;
		this.options.onStateChange('idle');
	}

	interrupt(): void {
		this.sendJson({ type: 'control', control: 'interrupt', data: {} });
	}

	userConfirm(event: UserConfirmResultEvent): void {
		if (this.controlChannel?.readyState !== 'open') {
			throw new Error('The realtime control channel is not open.');
		}
		this.controlChannel.send(
			JSON.stringify({ type: 'control', control: 'user_confirm', data: event }),
		);
	}

	private attachRemoteTrack(track: MediaStreamTrack): void {
		if (track.kind !== 'audio') return;
		this.remoteStream = new MediaStream([track]);
		const audio = new Audio();
		audio.autoplay = true;
		audio.srcObject = this.remoteStream;
		this.audioElement = audio;
		void audio.play().catch(() => {
			this.fail(new Error('The browser blocked realtime audio playback.'));
		});
	}

	private handleMessage(raw: unknown): void {
		if (typeof raw !== 'string') return;
		let frame: ServerFrame;
		try {
			frame = JSON.parse(raw) as ServerFrame;
		} catch {
			this.fail(new Error('The server sent an invalid realtime control frame.'));
			return;
		}

		if (frame.type === 'audio_start') {
			this.itemId = frame.item_id;
			this.itemTrackStartMs = frame.track_time_ms;
		} else if (frame.type === 'audio_duration') {
			this.durationsMs.set(frame.item_id, frame.duration_ms);
		} else if (frame.type === 'clear_audio') {
			this.acknowledgeClear(frame);
		} else if (frame.type === 'error') {
			this.fail(new Error(frame.detail));
		}
	}

	private acknowledgeClear(frame: ClearAudioFrame): void {
		const position = this.currentPosition();
		this.muteUntilTrackTime(frame.resume_track_time_ms);
		this.sendJson({
			type: 'playout_cleared',
			request_id: frame.request_id,
			...position,
		});
		if (position.item_id) this.durationsMs.delete(position.item_id);
		this.itemId = '';
		this.itemTrackStartMs = 0;
	}

	private muteUntilTrackTime(trackTimeMs: number): void {
		const audio = this.audioElement;
		if (!audio || !Number.isFinite(trackTimeMs)) return;
		if (this.playoutFenceTimer !== null) {
			window.clearTimeout(this.playoutFenceTimer);
		}
		audio.muted = true;
		const release = () => {
			if (this.audioElement !== audio) {
				this.playoutFenceTimer = null;
				return;
			}
			if (audio.currentTime * 1000 >= trackTimeMs) {
				audio.muted = false;
				this.playoutFenceTimer = null;
				return;
			}
			this.playoutFenceTimer = window.setTimeout(release, 20);
		};
		release();
	}

	private currentPosition(): { item_id: string; played_ms: number } {
		const currentTimeMs = (this.audioElement?.currentTime ?? 0) * 1000;
		if (!this.itemId || !Number.isFinite(currentTimeMs)) {
			return { item_id: '', played_ms: 0 };
		}
		const elapsed = Math.max(0, currentTimeMs - this.itemTrackStartMs);
		const duration = this.durationsMs.get(this.itemId) ?? 0;
		return {
			item_id: this.itemId,
			played_ms: Math.round(Math.min(elapsed, duration)),
		};
	}

	private reportPlayout(): void {
		const position = this.currentPosition();
		if (position.item_id) this.sendJson({ type: 'playout', ...position });
	}

	private sendJson(payload: Record<string, unknown>): void {
		if (this.controlChannel?.readyState === 'open') {
			this.controlChannel.send(JSON.stringify(payload));
		}
	}

	private throwIfStopped(): void {
		if (this.intentionalClose) {
			throw new Error('The realtime connection was stopped.');
		}
	}

	private waitForIceGathering(peerConnection: RTCPeerConnection): Promise<void> {
		if (peerConnection.iceGatheringState === 'complete') return Promise.resolve();
		return new Promise((resolve, reject) => {
			const timeout = window.setTimeout(() => {
				peerConnection.removeEventListener('icegatheringstatechange', onChange);
				reject(new Error('Timed out while gathering WebRTC network candidates.'));
			}, ICE_GATHERING_TIMEOUT_MS);
			const onChange = () => {
				if (peerConnection.iceGatheringState !== 'complete') return;
				window.clearTimeout(timeout);
				peerConnection.removeEventListener('icegatheringstatechange', onChange);
				resolve();
			};
			peerConnection.addEventListener('icegatheringstatechange', onChange);
		});
	}

	private waitForControlChannel(channel: RTCDataChannel): Promise<void> {
		if (channel.readyState === 'open') return Promise.resolve();
		return new Promise((resolve, reject) => {
			const cleanup = () => {
				window.clearTimeout(timeout);
				channel.removeEventListener('open', onOpen);
				channel.removeEventListener('close', onClose);
			};
			const timeout = window.setTimeout(() => {
				cleanup();
				reject(new Error('Timed out while opening the realtime connection.'));
			}, CONNECTION_TIMEOUT_MS);
			const onOpen = () => {
				cleanup();
				resolve();
			};
			const onClose = () => {
				cleanup();
				reject(this.failureError ?? new Error('The realtime connection was closed.'));
			};
			channel.addEventListener('open', onOpen);
			channel.addEventListener('close', onClose);
		});
	}

	private fail(error: Error): void {
		if (this.failed || this.intentionalClose) return;
		this.failed = true;
		this.failureError = error;
		if (this.active) this.options.onError(error);
		void this.stop().finally(() => this.options.onStateChange('error'));
	}
}
