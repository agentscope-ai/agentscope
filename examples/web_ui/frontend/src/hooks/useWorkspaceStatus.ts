import { useCallback, useEffect, useRef, useState } from 'react';

import { workspaceApi } from '@/api';
import type { WorkspaceStatus } from '@/api';

/**
 * Tracks where a session is pointed and the git state of that place.
 *
 * Not polled. Running git is a subprocess — a round trip too on a
 * sandboxed backend — and the two things that change the answer are both
 * observable: the user moving `cwd`, and a reply finishing. Callers
 * refetch on the latter and pass `cwd` so the former re-runs on its own.
 *
 * @param agentId - Agent owning the session. `null` clears the state.
 * @param sessionId - The session to report on. `null` clears the state.
 * @param cwd - The session's working directory, included only so a
 *   change to it triggers a refetch; the server reads the stored value.
 * @returns The status, a `loading` flag and a `refetch` for manual use.
 */
export function useWorkspaceStatus(
	agentId: string | null,
	sessionId: string | null,
	cwd: string | null,
) {
	const [status, setStatus] = useState<WorkspaceStatus | null>(null);
	const [loading, setLoading] = useState(false);
	// Bumped by every call, so only the newest one may write. Two git
	// subprocesses per request means a slow workspace can answer after
	// the user has already moved on, and nothing here polls — a stale
	// branch would stay on screen until the next reply ends.
	const reqId = useRef(0);

	const refetch = useCallback(async () => {
		const id = ++reqId.current;
		if (!agentId || !sessionId) {
			setStatus(null);
			return;
		}
		setLoading(true);
		try {
			const next = await workspaceApi.status(agentId, sessionId);
			if (id === reqId.current) setStatus(next);
		} catch {
			// A workspace that cannot be reached is reported the same way
			// as one without a repository: no badge. `silent` on the call
			// already suppressed the toast.
			if (id === reqId.current) setStatus(null);
		} finally {
			// Guarded too: an overtaken request must not clear the
			// spinner while its replacement is still in flight.
			if (id === reqId.current) setLoading(false);
		}
		// `cwd` is not read here — it is a dependency so that moving the
		// session re-runs this.
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [agentId, sessionId, cwd]);

	useEffect(() => {
		void refetch();
	}, [refetch]);

	return { status, loading, refetch };
}
