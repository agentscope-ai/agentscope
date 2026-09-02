import { useEffect, useRef, useState } from 'react';

/**
 * Parse API / SSE ISO timestamps into epoch ms.
 *
 * AgentScope emits UTC ISO 8601 strings (``Z`` suffix). For backward
 * compatibility, naive strings without a zone are treated as UTC.
 */
export function parseApiTimestamp(value?: string | null): number | null {
	if (!value) return null;
	const trimmed = value.trim();
	if (!trimmed) return null;
	if (/[zZ]$/.test(trimmed) || /[+-]\d{2}:\d{2}$/.test(trimmed)) {
		const ms = Date.parse(trimmed);
		return Number.isNaN(ms) ? null : ms;
	}
	const ms = Date.parse(`${trimmed}Z`);
	return Number.isNaN(ms) ? null : ms;
}

/**
 * Elapsed seconds for a block or message that may still be streaming.
 *
 * - **Finished**: ``finished_at - created_at`` (both from server).
 * - **Running**: anchor to local mount time so we never mix server
 *   ``created_at`` with ``Date.now()`` (avoids UTC-offset flashes like
 *   "8h" while streaming in UTC+8 browsers).
 */
export function useElapsedSeconds(
	createdAt: string | undefined,
	finishedAt: string | null | undefined,
	resetKey?: string,
): number {
	const isRunning = !finishedAt;
	const [now, setNow] = useState(() => Date.now());
	const runningStartRef = useRef<number | null>(null);

	useEffect(() => {
		runningStartRef.current = null;
	}, [resetKey]);

	useEffect(() => {
		if (!isRunning) return;
		const id = setInterval(() => setNow(Date.now()), 1000);
		return () => clearInterval(id);
	}, [isRunning]);

	if (!isRunning) {
		const startMs = parseApiTimestamp(createdAt);
		const endMs = parseApiTimestamp(finishedAt);
		if (startMs != null && endMs != null) {
			return Math.max(0, (endMs - startMs) / 1000);
		}
	}

	if (runningStartRef.current === null) {
		runningStartRef.current = Date.now();
	}
	return Math.max(0, (now - runningStartRef.current) / 1000);
}
