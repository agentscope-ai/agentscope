import {
	createContext,
	useCallback,
	useContext,
	useEffect,
	useMemo,
	useReducer,
	useRef,
	type ReactNode,
} from 'react';

import { MAX_CONCURRENT_UPLOADS, isTerminal, type UploadTask } from './uploadTypes';
import { knowledgeBaseApi } from '@/api';
import type { KnowledgeDocumentStatus } from '@/api';

/**
 * Public contract the surrounding app sees. Exposed via the React
 * context value.
 */
interface UploadContextValue {
	tasks: UploadTask[];
	/** Enqueue one or more files against a knowledge base. */
	enqueue: (knowledgeBaseId: string, files: File[]) => UploadTask[];
	/**
	 * Abort an upload task. Effects depend on the current phase:
	 *
	 * - `queued` — removed from the queue.
	 * - `uploading` — XHR is aborted, then the task transitions to
	 *   `cancelled`.
	 * - server-side phases — no-op (the worker has already taken over;
	 *   route through the regular document-delete flow instead).
	 * - terminal phases — equivalent to `dismiss`.
	 */
	cancel: (taskId: string) => void;
	/** Drop a terminal task from the list (UI dismissal). */
	dismiss: (taskId: string) => void;
	/** Drop every terminal task for a knowledge base. */
	clearFinishedForKb: (knowledgeBaseId: string) => void;
	/**
	 * Filtered tasks for a knowledge base id. Cached by reference per
	 * (knowledgeBaseId, tasks-list-identity) so consumers can pass it
	 * straight into React dependency arrays.
	 */
	tasksForKb: (knowledgeBaseId: string) => UploadTask[];
	/**
	 * Apply server-side status snapshots from the polling hook.
	 * Lookup is keyed by `documentId`; tasks without a documentId
	 * (still uploading) are skipped.
	 */
	applyServerStatuses: (
		knowledgeBaseId: string,
		items: Array<{
			id: string;
			status: KnowledgeDocumentStatus;
			error: string | null;
		}>,
	) => void;
	/**
	 * Document ids the consumer should be polling. Filtered to tasks
	 * that have a documentId AND a non-terminal server-side phase.
	 */
	pollableDocumentIds: (knowledgeBaseId: string) => string[];
}

const UploadContext = createContext<UploadContextValue | null>(null);

// ───────── Reducer ────────────────────────────────────────────────────

type Action =
	| { type: 'ADD'; tasks: UploadTask[] }
	| { type: 'START_UPLOAD'; taskId: string }
	| { type: 'PROGRESS'; taskId: string; loaded: number; total: number }
	| {
			type: 'UPLOAD_DONE';
			taskId: string;
			documentId: string;
			status: KnowledgeDocumentStatus;
	  }
	| { type: 'UPLOAD_FAILED'; taskId: string; error: string }
	| { type: 'CANCEL'; taskId: string }
	| { type: 'REMOVE'; taskId: string }
	| {
			type: 'SERVER_STATUS';
			knowledgeBaseId: string;
			items: Array<{
				id: string;
				status: KnowledgeDocumentStatus;
				error: string | null;
			}>;
	  }
	| { type: 'CLEAR_FINISHED'; knowledgeBaseId: string };

function reducer(state: UploadTask[], action: Action): UploadTask[] {
	switch (action.type) {
		case 'ADD':
			return [...state, ...action.tasks];
		case 'START_UPLOAD':
			return state.map((t) =>
				t.taskId === action.taskId ? { ...t, phase: 'uploading', loaded: 0 } : t,
			);
		case 'PROGRESS':
			return state.map((t) =>
				t.taskId === action.taskId
					? {
							...t,
							loaded: action.loaded,
							// Allow upload progress to refine an unknown size
							// (browser couldn't compute it at enqueue time).
							size: t.size || action.total,
						}
					: t,
			);
		case 'UPLOAD_DONE':
			return state.map((t) =>
				t.taskId === action.taskId
					? {
							...t,
							documentId: action.documentId,
							phase: action.status,
							loaded: t.size,
						}
					: t,
			);
		case 'UPLOAD_FAILED':
			return state.map((t) =>
				t.taskId === action.taskId ? { ...t, phase: 'error', error: action.error } : t,
			);
		case 'CANCEL':
			return state.map((t) =>
				t.taskId === action.taskId ? { ...t, phase: 'cancelled' } : t,
			);
		case 'REMOVE':
			return state.filter((t) => t.taskId !== action.taskId);
		case 'SERVER_STATUS': {
			const byId = new Map(action.items.map((i) => [i.id, i]));
			return state.map((t) => {
				if (t.knowledgeBaseId !== action.knowledgeBaseId) return t;
				if (!t.documentId) return t;
				const match = byId.get(t.documentId);
				if (!match) return t;
				// Client-side terminal phases (`cancelled`) win — the
				// server might still drive the doc to `ready`, but the
				// user has signalled they no longer care.
				if (t.phase === 'cancelled') return t;
				if (t.phase === match.status && t.error === match.error) {
					return t;
				}
				return {
					...t,
					phase: match.status,
					error: match.error,
				};
			});
		}
		case 'CLEAR_FINISHED':
			return state.filter(
				(t) => t.knowledgeBaseId !== action.knowledgeBaseId || !isTerminal(t.phase),
			);
	}
}

function newTaskId(): string {
	if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
		return crypto.randomUUID();
	}
	return `upload-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

interface UploadProviderProps {
	children: ReactNode;
}

/**
 * Mutable side-state for XHR controllers and scheduling flags.
 *
 * Lazy-initialised inside `useRef` (the factory form) so we never read
 * `.current` during the initial render — and because the structure is
 * a fixed shape of mutable maps, we can stash everything in a single
 * `useRef` without violating the "no reading refs during render" rule:
 * the reducer is the source of truth for render-relevant state, and
 * these maps hold only opaque side-effects (Files, AbortControllers).
 */
interface UploadProviderRefs {
	/** Map of taskId → File payload (kept off state; not serialisable). */
	files: Map<string, File>;
	/** Map of taskId → AbortController to cancel the in-flight XHR. */
	controllers: Map<string, AbortController>;
	/** Tasks the scheduler has already kicked off this lifetime. */
	started: Set<string>;
}

export function UploadProvider({ children }: UploadProviderProps) {
	const [tasks, dispatch] = useReducer(reducer, [] as UploadTask[]);
	const refsRef = useRef<UploadProviderRefs | null>(null);
	if (refsRef.current === null) {
		refsRef.current = {
			files: new Map(),
			controllers: new Map(),
			started: new Set(),
		};
	}

	const startUpload = useCallback((task: UploadTask) => {
		const refs = refsRef.current!;
		const file = refs.files.get(task.taskId);
		if (!file) {
			dispatch({
				type: 'UPLOAD_FAILED',
				taskId: task.taskId,
				error: 'Internal error: missing file payload.',
			});
			return;
		}
		const controller = new AbortController();
		refs.controllers.set(task.taskId, controller);
		dispatch({ type: 'START_UPLOAD', taskId: task.taskId });

		knowledgeBaseApi
			.uploadDocument(task.knowledgeBaseId, file, {
				signal: controller.signal,
				onProgress: ({ loaded, total }) => {
					dispatch({
						type: 'PROGRESS',
						taskId: task.taskId,
						loaded,
						total,
					});
				},
			})
			.then((response) => {
				dispatch({
					type: 'UPLOAD_DONE',
					taskId: task.taskId,
					documentId: response.document_id,
					status: response.status,
				});
			})
			.catch((err: unknown) => {
				if (err instanceof DOMException && err.name === 'AbortError') {
					// Already handled by the cancel() path.
					return;
				}
				const message = err instanceof Error ? err.message : 'Upload failed.';
				dispatch({
					type: 'UPLOAD_FAILED',
					taskId: task.taskId,
					error: message,
				});
			})
			.finally(() => {
				refs.controllers.delete(task.taskId);
				refs.files.delete(task.taskId);
			});
	}, []);

	// Scheduler — fires after every state change. Idempotent thanks to
	// `refs.started`, so StrictMode's double-effect is harmless.
	useEffect(() => {
		const refs = refsRef.current!;
		const running = tasks.filter((t) => t.phase === 'uploading').length;
		const queued = tasks.filter((t) => t.phase === 'queued');
		const slots = MAX_CONCURRENT_UPLOADS - running;
		if (slots <= 0 || queued.length === 0) return;
		for (const task of queued.slice(0, slots)) {
			if (refs.started.has(task.taskId)) continue;
			refs.started.add(task.taskId);
			startUpload(task);
		}
	}, [tasks, startUpload]);

	const enqueue = useCallback((knowledgeBaseId: string, files: File[]): UploadTask[] => {
		const refs = refsRef.current!;
		const now = Date.now();
		const newTasks = files.map((file): UploadTask => {
			const taskId = newTaskId();
			refs.files.set(taskId, file);
			return {
				taskId,
				knowledgeBaseId,
				filename: file.name,
				size: file.size,
				documentId: null,
				phase: 'queued',
				loaded: 0,
				error: null,
				createdAt: now,
			};
		});
		dispatch({ type: 'ADD', tasks: newTasks });
		return newTasks;
	}, []);

	const cancel = useCallback(
		(taskId: string) => {
			const refs = refsRef.current!;
			const task = tasks.find((t) => t.taskId === taskId);
			if (!task) return;
			if (task.phase === 'queued') {
				refs.files.delete(taskId);
				dispatch({ type: 'REMOVE', taskId });
				return;
			}
			if (task.phase === 'uploading') {
				refs.controllers.get(taskId)?.abort();
				dispatch({ type: 'CANCEL', taskId });
				return;
			}
			if (isTerminal(task.phase)) {
				dispatch({ type: 'REMOVE', taskId });
			}
			// Server-side phases are not cancellable here; the caller
			// should use the regular document-delete API instead.
		},
		[tasks],
	);

	const dismiss = useCallback((taskId: string) => {
		dispatch({ type: 'REMOVE', taskId });
	}, []);

	const clearFinishedForKb = useCallback((knowledgeBaseId: string) => {
		dispatch({ type: 'CLEAR_FINISHED', knowledgeBaseId });
	}, []);

	const applyServerStatuses = useCallback<UploadContextValue['applyServerStatuses']>(
		(knowledgeBaseId, items) => {
			dispatch({ type: 'SERVER_STATUS', knowledgeBaseId, items });
		},
		[],
	);

	// `tasksForKb` and `pollableDocumentIds` need stable references per
	// (knowledgeBaseId, tasks) tuple so downstream effects don't churn.
	const byKb = useMemo(() => {
		const out = new Map<string, UploadTask[]>();
		for (const t of tasks) {
			const list = out.get(t.knowledgeBaseId);
			if (list) list.push(t);
			else out.set(t.knowledgeBaseId, [t]);
		}
		return out;
	}, [tasks]);

	const tasksForKb = useCallback(
		(knowledgeBaseId: string): UploadTask[] => byKb.get(knowledgeBaseId) ?? [],
		[byKb],
	);

	const pollableDocumentIds = useCallback(
		(knowledgeBaseId: string): string[] => {
			const list = byKb.get(knowledgeBaseId);
			if (!list) return [];
			const out: string[] = [];
			for (const t of list) {
				if (!t.documentId) continue;
				// `uploading` / `queued` have no documentId yet; only
				// server-side non-terminal phases are pollable.
				if (
					t.phase === 'pending' ||
					t.phase === 'parsing' ||
					t.phase === 'chunking' ||
					t.phase === 'indexing'
				) {
					out.push(t.documentId);
				}
			}
			return out;
		},
		[byKb],
	);

	// Warn before navigation when in-flight tasks would be lost.
	// `uploading` tasks die with the page; server-side phases survive,
	// so they do not trigger the warning.
	useEffect(() => {
		const hasInFlightUpload = tasks.some(
			(t) => t.phase === 'queued' || t.phase === 'uploading',
		);
		if (!hasInFlightUpload) return;
		const handler = (e: BeforeUnloadEvent) => {
			e.preventDefault();
			// Chrome / Edge require returnValue to be set.
			e.returnValue = '';
		};
		window.addEventListener('beforeunload', handler);
		return () => window.removeEventListener('beforeunload', handler);
	}, [tasks]);

	const value = useMemo<UploadContextValue>(
		() => ({
			tasks,
			enqueue,
			cancel,
			dismiss,
			clearFinishedForKb,
			tasksForKb,
			applyServerStatuses,
			pollableDocumentIds,
		}),
		[
			tasks,
			enqueue,
			cancel,
			dismiss,
			clearFinishedForKb,
			tasksForKb,
			applyServerStatuses,
			pollableDocumentIds,
		],
	);

	return <UploadContext.Provider value={value}>{children}</UploadContext.Provider>;
}

export function useUploadContext(): UploadContextValue {
	const ctx = useContext(UploadContext);
	if (!ctx) {
		throw new Error('useUploadContext must be used inside <UploadProvider>');
	}
	return ctx;
}
