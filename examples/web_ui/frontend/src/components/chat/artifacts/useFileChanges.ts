import { getContentBlocks, type Msg } from '@agentscope-ai/agentscope/message';
import { useEffect, useRef, useState } from 'react';

import { extractFileChanges, type FileChanges, filePathKey } from './artifacts';

const EMPTY_CHANGES: FileChanges = new Map();

/** Batch live tool completions; never replay history or another session. */
export function useFileChanges(msgs: Msg[], scope: string, loading: boolean): FileChanges {
	const [published, setPublished] = useState({ scope, changes: EMPTY_CHANGES });
	const seen = useRef<Map<string, Set<string>> | null>(null);
	const pending = useRef(new Set<string>());
	const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
	const sequence = useRef(0);

	useEffect(() => {
		seen.current = null;
		pending.current.clear();
		setPublished({ scope, changes: EMPTY_CHANGES });
		return () => {
			if (timer.current !== null) clearTimeout(timer.current);
			timer.current = null;
		};
	}, [scope, loading]);

	useEffect(() => {
		if (loading) return;
		const baseline = seen.current === null;
		seen.current ??= new Map();
		for (const msg of msgs) {
			if (msg.role !== 'assistant') continue;
			const calls = seen.current.get(msg.id) ?? new Set<string>();
			for (const change of extractFileChanges(getContentBlocks(msg), calls)) {
				calls.add(change.id);
				if (!baseline) pending.current.add(filePathKey(change.path));
			}
			seen.current.set(msg.id, calls);
		}
		if (!pending.current.size || timer.current !== null) return;
		// A bounded batching window avoids starvation during a long reply.
		timer.current = setTimeout(() => {
			timer.current = null;
			const paths = [...pending.current];
			pending.current.clear();
			const version = ++sequence.current;
			setPublished((previous) => {
				const changes = new Map(previous.scope === scope ? previous.changes : []);
				for (const path of paths) changes.set(path, version);
				return { scope, changes };
			});
		}, 250);
	}, [msgs, scope, loading]);

	return !loading && published.scope === scope ? published.changes : EMPTY_CHANGES;
}
