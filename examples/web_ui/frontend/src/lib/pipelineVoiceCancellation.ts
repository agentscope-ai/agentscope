import type { AgentEvent } from '@agentscope-ai/agentscope/event';

import type { PipelineVoiceSession } from '@/lib/pipelineVoiceSession';

const pending = new Map<string, PipelineVoiceSession>();

export function getPendingPipelineVoice(key: string): PipelineVoiceSession | undefined {
	return pending.get(key);
}

/** Retain cancellation, but no microphone or playback, across viewport unmounts. */
export function retainPipelineVoice(
	key: string,
	session: PipelineVoiceSession,
	events: (signal: AbortSignal) => AsyncGenerator<AgentEvent>,
): void {
	void session.stop();
	if (!session.restartBlocked || pending.get(key) === session) return;
	pending.set(key, session);
	const controller = new AbortController();
	const unsubscribe = session.subscribe({
		onState: () => finish(),
		onError: () => undefined,
	});
	function finish(): void {
		if (session.restartBlocked) return;
		controller.abort();
		unsubscribe();
		if (pending.get(key) === session) pending.delete(key);
	}
	if (!session.needsCancellation) return;
	void (async () => {
		while (!controller.signal.aborted) {
			const onEvent = session.cancellationEventHandler();
			try {
				for await (const event of events(controller.signal)) {
					if (controller.signal.aborted) break;
					onEvent(event);
				}
			} catch {
				// Keep the turn tracked and reconnect; a transport error is not completion.
			}
			if (controller.signal.aborted) break;
			await new Promise<void>((resolve) => {
				const timer = setTimeout(done, 2000);
				function done() {
					clearTimeout(timer);
					controller.signal.removeEventListener('abort', done);
					resolve();
				}
				controller.signal.addEventListener('abort', done, { once: true });
			});
		}
	})();
}
