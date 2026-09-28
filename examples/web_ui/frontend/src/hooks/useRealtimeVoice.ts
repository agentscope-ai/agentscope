import type { UserConfirmResultEvent } from '@agentscope-ai/agentscope/event';
import { useCallback, useEffect, useRef, useState } from 'react';
import { toast } from 'sonner';

import { BrowserWebRTCTransport, type RealtimeConnectionState } from '@/lib/browserWebRTCTransport';

export function useRealtimeVoice(agentId: string | null, sessionId: string | null) {
	const [state, setState] = useState<RealtimeConnectionState>('idle');
	const transportRef = useRef<BrowserWebRTCTransport | null>(null);

	const stop = useCallback(async () => {
		const transport = transportRef.current;
		transportRef.current = null;
		if (transport) await transport.stop();
		setState('idle');
	}, []);

	useEffect(() => {
		setState('idle');
		return () => {
			const transport = transportRef.current;
			if (transport) {
				transportRef.current = null;
				void transport.stop();
			}
		};
	}, [agentId, sessionId]);

	const start = useCallback(async () => {
		if (!agentId || !sessionId || transportRef.current) return;
		const transport = new BrowserWebRTCTransport({
			agentId,
			sessionId,
			onStateChange: (nextState) => {
				if (transportRef.current === transport) setState(nextState);
			},
			onError: (error) => {
				if (transportRef.current !== transport) return;
				transportRef.current = null;
				setState('error');
				toast.error(error.message);
			},
		});
		transportRef.current = transport;
		try {
			await transport.start();
		} catch (error) {
			await transport.stop();
			if (transportRef.current === transport) {
				transportRef.current = null;
				setState('error');
				toast.error(error instanceof Error ? error.message : 'Voice mode failed to start.');
			}
		}
	}, [agentId, sessionId]);

	const toggle = useCallback(async () => {
		if (state === 'active' || state === 'connecting') await stop();
		else await start();
	}, [start, state, stop]);

	const interrupt = useCallback(() => {
		transportRef.current?.interrupt();
	}, []);

	const userConfirm = useCallback(async (event: UserConfirmResultEvent) => {
		const transport = transportRef.current;
		if (!transport) throw new Error('Realtime voice mode is not active.');
		transport.userConfirm(event);
	}, []);

	return { state, start, stop, toggle, interrupt, userConfirm };
}
