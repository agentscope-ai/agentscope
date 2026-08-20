import type { KnowledgeDocumentStatus } from '@/api';

/**
 * Lifecycle states a client-visible upload task can be in.
 *
 * The first two (`queued`, `uploading`) are pure client states. After
 * the upload endpoint returns we mirror the server's
 * :type:`KnowledgeDocumentStatus`. `cancelled` is also a client-side
 * terminal — set when the user aborts before the upload completes.
 */
export type UploadPhase = 'queued' | 'uploading' | KnowledgeDocumentStatus | 'cancelled';

export interface UploadTask {
	/** Stable client-side id; survives across React renders. */
	taskId: string;
	knowledgeBaseId: string;
	filename: string;
	/** Total bytes; `0` only when the browser cannot compute it. */
	size: number;
	/** Server-assigned document id — `null` until upload returns 201. */
	documentId: string | null;
	phase: UploadPhase;
	/** Bytes uploaded so far; meaningful only during `uploading`. */
	loaded: number;
	/** Human-readable failure reason; non-null only when `phase === 'error'`. */
	error: string | null;
	createdAt: number;
}

export function isTerminal(phase: UploadPhase): boolean {
	return phase === 'ready' || phase === 'error' || phase === 'cancelled';
}

export function isInFlight(phase: UploadPhase): boolean {
	return !isTerminal(phase);
}

/**
 * Maximum number of upload tasks transferring bytes at once. Queued
 * tasks above this cap wait until a slot frees up. Server-side phases
 * (`pending`/`parsing`/…) do not count against the cap — they finish
 * out of band.
 */
export const MAX_CONCURRENT_UPLOADS = 3;
