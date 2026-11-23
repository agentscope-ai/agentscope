import {
	createContext,
	useCallback,
	useContext,
	useEffect,
	useMemo,
	useRef,
	useState,
	useSyncExternalStore,
} from 'react';
import type { ReactNode } from 'react';

import { StreamingAudioManager } from '@/utils/streamingAudio';
import type { StreamingAudioState } from '@/utils/streamingAudio';

const AudioContext = createContext<StreamingAudioManager | null>(null);

interface ReplayController {
	blocked: boolean;
	setBlocked: (blocked: boolean) => void;
	play: (el: HTMLAudioElement) => Promise<void>;
	stop: () => void;
}

const ReplayContext = createContext<ReplayController | null>(null);

/**
 * Provides a {@link StreamingAudioManager} to the component tree. The manager
 * collects DATA_BLOCK_* events for audio blocks and exposes per-block state
 * that ``MessageBubble`` consumes via {@link useAudioBlock}.
 *
 * Mount this once around any subtree that renders assistant messages.
 */
export function AudioProvider({ children }: { children: ReactNode }) {
	const manager = useMemo(() => new StreamingAudioManager(), []);
	useEffect(() => () => manager.disposeAll(), [manager]);

	const currentRef = useRef<HTMLAudioElement | null>(null);
	const blockedRef = useRef(false);
	const [blocked, setBlockedState] = useState(false);
	const stop = useCallback(() => {
		currentRef.current?.pause();
		currentRef.current = null;
	}, []);
	const setBlocked = useCallback(
		(next: boolean) => {
			// Guard play() immediately, including clicks before the button re-renders.
			if (blockedRef.current === next) return;
			blockedRef.current = next;
			if (next) stop();
			setBlockedState(next);
		},
		[stop],
	);
	const replay: ReplayController = useMemo(
		() => ({
			blocked,
			setBlocked,
			stop,
			async play(el: HTMLAudioElement) {
				if (blockedRef.current) return;
				manager.stopAutoplay();
				if (currentRef.current && currentRef.current !== el) {
					currentRef.current.pause();
					currentRef.current.currentTime = 0;
				}
				currentRef.current = el;
				await el.play();
			},
		}),
		[manager, blocked, setBlocked, stop],
	);

	return (
		<AudioContext.Provider value={manager}>
			<ReplayContext.Provider value={replay}>{children}</ReplayContext.Provider>
		</AudioContext.Provider>
	);
}

/**
 * Access the streaming audio manager. Returns ``null`` outside of an
 * ``AudioProvider`` — callers must handle that, since audio handling is
 * an optional feature.
 */
export function useAudioManager(): StreamingAudioManager | null {
	return useContext(AudioContext);
}

/**
 * Access the replay controller that ensures only one audio element plays
 * at a time across all message bubbles.
 */
export function useReplayController(): ReplayController | null {
	return useContext(ReplayContext);
}

/**
 * Subscribe to the streaming state for a single audio DataBlock.
 * Re-renders when the block transitions from ``streaming`` to ``ready``.
 *
 * Returns ``null`` if the block isn't being tracked (e.g. a historical
 * message loaded from the server, where the bytes are already complete).
 */
export function useAudioBlock(blockId: string | undefined): StreamingAudioState | null {
	const manager = useAudioManager();
	return useSyncExternalStore(
		(fn) => {
			if (!manager || !blockId) return () => undefined;
			return manager.subscribe(blockId, fn);
		},
		() => (manager && blockId ? manager.getState(blockId) : null),
		() => null,
	);
}
