import { asrApi } from '@/api/asr';
import { getBaseUrl } from '@/api/client';

export interface RealtimeASRConfig {
	credentialId: string;
	model: string;
	parameters: Record<string, unknown>;
}

interface RealtimeASRTransportOptions {
	config: RealtimeASRConfig;
	onTranscript: (text: string, isLast: boolean) => void;
}

interface ServerEvent {
	type: 'ready' | 'transcript' | 'error';
	text?: string;
	is_last?: boolean;
	detail?: string;
}

/** Browser WebSocket transport for server-proxied realtime ASR. */
export class BrowserRealtimeASRTransport {
	private readonly options: RealtimeASRTransportOptions;
	private socket: WebSocket | null = null;
	private readyResolve: (() => void) | null = null;
	private readyReject: ((error: Error) => void) | null = null;
	private finalResolve: (() => void) | null = null;
	private finalReject: ((error: Error) => void) | null = null;
	private closedByClient = false;

	constructor(options: RealtimeASRTransportOptions) {
		this.options = options;
	}

	async connect(sampleRate: number): Promise<void> {
		const { token } = await asrApi.createRealtimeToken(
			this.options.config.credentialId,
			this.options.config.model,
		);
		if (this.closedByClient) {
			throw new Error('Realtime ASR connection was cancelled.');
		}
		const baseUrl = getBaseUrl() || window.location.origin;
		const url = new URL('/asr/realtime', baseUrl);
		url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
		url.searchParams.set('token', token);

		const socket = new WebSocket(url);
		this.socket = socket;
		const ready = new Promise<void>((resolve, reject) => {
			this.readyResolve = resolve;
			this.readyReject = reject;
		});

		socket.onopen = () => {
			socket.send(
				JSON.stringify({
					type: 'start',
					credential_id: this.options.config.credentialId,
					model: this.options.config.model,
					parameters: this.options.config.parameters,
					sample_rate: sampleRate,
				}),
			);
		};
		socket.onmessage = (event) => this.handleMessage(event.data);
		socket.onerror = () => this.fail(new Error('Realtime ASR connection failed.'));
		socket.onclose = () => {
			if (!this.closedByClient && (this.readyReject || this.finalReject)) {
				this.fail(new Error('Realtime ASR connection closed unexpectedly.'));
			}
		};

		return ready;
	}

	sendAudio(audio: ArrayBuffer): void {
		if (this.socket?.readyState !== WebSocket.OPEN) return;
		this.socket.send(audio);
	}

	async stop(): Promise<void> {
		if (this.socket?.readyState !== WebSocket.OPEN) {
			throw new Error('Realtime ASR connection is not open.');
		}
		const final = new Promise<void>((resolve, reject) => {
			this.finalResolve = resolve;
			this.finalReject = reject;
		});
		this.socket.send(JSON.stringify({ type: 'stop' }));
		return final;
	}

	close(): void {
		this.closedByClient = true;
		this.readyReject?.(new Error('Realtime ASR connection was cancelled.'));
		this.finalReject?.(new Error('Realtime ASR connection was cancelled.'));
		this.clearPending();
		this.socket?.close();
		this.socket = null;
	}

	private handleMessage(data: unknown): void {
		if (typeof data !== 'string') return;
		let event: ServerEvent;
		try {
			event = JSON.parse(data) as ServerEvent;
		} catch {
			this.fail(new Error('Realtime ASR returned an invalid event.'));
			return;
		}
		if (event.type === 'ready') {
			this.readyResolve?.();
			this.readyResolve = null;
			this.readyReject = null;
			return;
		}
		if (event.type === 'error') {
			this.fail(new Error(event.detail || 'Realtime ASR failed.'));
			return;
		}
		if (event.type === 'transcript') {
			this.options.onTranscript(event.text ?? '', !!event.is_last);
			if (event.is_last) {
				this.finalResolve?.();
				this.finalResolve = null;
				this.finalReject = null;
			}
		}
	}

	private fail(error: Error): void {
		this.readyReject?.(error);
		this.finalReject?.(error);
		this.clearPending();
		this.closedByClient = true;
		this.socket?.close();
	}

	private clearPending(): void {
		this.readyResolve = null;
		this.readyReject = null;
		this.finalResolve = null;
		this.finalReject = null;
	}
}
