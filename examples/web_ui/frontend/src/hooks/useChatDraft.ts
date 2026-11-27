import type { ContentBlock } from '@agentscope-ai/agentscope/message';
import { useCallback, useSyncExternalStore } from 'react';

export interface ProcessedFile {
	name: string;
	status: 'processing' | 'done';
	block: ContentBlock | null;
}

interface ChatDraft {
	value: string;
	files: ProcessedFile[];
}

const empty: ChatDraft = { value: '', files: [] };
const drafts = new Map<string, ChatDraft>();
const changes = new EventTarget();

/** Keep a tab's drafts available to late submissions and remounted composers. */
export function useChatDraft(key: string) {
	const subscribe = useCallback(
		(notify: () => void) => {
			changes.addEventListener(key, notify);
			return () => changes.removeEventListener(key, notify);
		},
		[key],
	);
	const read = () => drafts.get(key) ?? empty;
	const draft = useSyncExternalStore(subscribe, read);
	const updateDraft = (change: (current: ChatDraft) => ChatDraft) => {
		const next = change(read());
		if (next.value || next.files.length) drafts.set(key, next);
		else drafts.delete(key);
		changes.dispatchEvent(new Event(key));
	};
	return { draft, updateDraft };
}
