import { useEffect, useMemo, useRef, useState } from 'react';

import { knowledgeBaseApi } from '@/api';
import type { KnowledgeDocumentView } from '@/api';
import { useUploadContext } from '@/context/UploadContext';

const POLL_INTERVAL_MS = 1500;

/**
 * Document ids the polling loop should track each tick. Combines:
 *
 * - documents the local `UploadProvider` is still expecting transitions
 *   for (so a freshly-uploaded file polls without waiting for the next
 *   list refresh);
 * - documents the caller knows are non-terminal from a server list
 *   (so a page reload mid-indexing keeps polling without lifting the
 *   upload through the provider).
 *
 * Ids that resolve to a terminal state (`ready` / `error`) drop out
 * automatically — the polling loop stops as soon as the union becomes
 * empty.
 */
interface PollingInput {
	knowledgeBaseId: string | null;
	/** Currently-listed server documents. */
	documents: KnowledgeDocumentView[];
}

interface PollingResult {
	/** Most-recent status views, keyed by document id. */
	statuses: Record<string, KnowledgeDocumentView>;
	/** True while the loop is actively scheduling refreshes. */
	polling: boolean;
}

/**
 * Conditional polling — only schedules a request when there is at least
 * one non-terminal document id to watch. The loop self-stops as soon as
 * every tracked id reaches `ready` / `error`, and rewires automatically
 * when new ids enter the set (a fresh upload, a re-mount, etc.).
 *
 * Single source of truth for the `UploadProvider`'s server-side phase
 * mirror: every tick that returns items is fanned out through
 * `applyServerStatuses`, so the in-flight upload cards reflect the
 * worker's progress without any extra wiring at the call site.
 */
export function useDocumentStatusPolling({
	knowledgeBaseId,
	documents,
}: PollingInput): PollingResult {
	const { pollableDocumentIds, applyServerStatuses } = useUploadContext();

	// Server-side non-terminal docs from the list (catches reload state).
	const docIds = useMemo(() => {
		const out: string[] = [];
		for (const d of documents) {
			if (d.status !== 'ready' && d.status !== 'error') {
				out.push(d.id);
			}
		}
		return out;
	}, [documents]);

	// Stable, sorted union of server-side non-terminal docs (catches
	// reload state) and locally-tracked in-flight docs from the upload
	// provider. Used both as the request payload and as the effect
	// dependency key so dropping/adding a single id triggers exactly
	// one effect re-run.
	const watchIdsKey = useMemo(() => {
		const set = new Set<string>(docIds);
		if (knowledgeBaseId) {
			for (const id of pollableDocumentIds(knowledgeBaseId)) {
				set.add(id);
			}
		}
		return Array.from(set).sort().join(',');
	}, [docIds, knowledgeBaseId, pollableDocumentIds]);

	const [statuses, setStatuses] = useState<Record<string, KnowledgeDocumentView>>({});

	// Mutable refs keep the running interval alive across re-renders
	// without forcing the effect to restart on every state change.
	const inflightRef = useRef<AbortController | null>(null);

	useEffect(() => {
		if (!knowledgeBaseId) return;
		const ids = watchIdsKey ? watchIdsKey.split(',') : [];
		if (ids.length === 0) return;

		let cancelled = false;

		const tick = async () => {
			if (cancelled) return;
			inflightRef.current?.abort();
			const controller = new AbortController();
			inflightRef.current = controller;
			try {
				const { items } = await knowledgeBaseApi.getDocumentStatus(knowledgeBaseId, ids);
				if (cancelled) return;
				setStatuses((prev) => {
					const next = { ...prev };
					for (const item of items) next[item.id] = item;
					return next;
				});
				applyServerStatuses(
					knowledgeBaseId,
					items.map((i) => ({
						id: i.id,
						status: i.status,
						error: i.error,
					})),
				);
			} catch {
				// Swallow transient errors — the next tick will retry.
				// We deliberately don't surface a toast: a flaky network
				// shouldn't spam the user while indexing is in the
				// background.
			}
		};

		// Fire immediately so the first reading lands without waiting a
		// full poll interval (important right after an upload).
		void tick();
		const handle = window.setInterval(tick, POLL_INTERVAL_MS);
		return () => {
			cancelled = true;
			inflightRef.current?.abort();
			window.clearInterval(handle);
		};
	}, [knowledgeBaseId, watchIdsKey, applyServerStatuses]);

	return {
		statuses,
		polling: watchIdsKey.length > 0,
	};
}
