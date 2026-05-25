import { useState, useEffect, useCallback } from 'react';

import { skillApi } from '../api';
import type { Skill } from '../api';

/**
 * Manages skills available in a session's workspace.
 * Re-fetches whenever agentId or sessionId changes.
 *
 * @param agentId   - The owning agent. Pass null to skip fetching.
 * @param sessionId - The target session. Pass null to skip fetching.
 */
export function useSkills(agentId: string | null, sessionId: string | null) {
	const [skills, setSkills] = useState<Skill[]>([]);
	const [loading, setLoading] = useState(false);
	const [error, setError] = useState<Error | null>(null);

	const refetch = useCallback(async () => {
		if (!agentId || !sessionId) {
			setSkills([]);
			return;
		}
		setLoading(true);
		setError(null);
		try {
			setSkills(await skillApi.list(agentId, sessionId));
		} catch (e) {
			setError(e as Error);
		} finally {
			setLoading(false);
		}
	}, [agentId, sessionId]);

	useEffect(() => {
		refetch();
	}, [refetch]);

	/** Adds a skill from the given path and refreshes the list. */
	const add = useCallback(
		async (skillPath: string) => {
			if (!agentId || !sessionId) throw new Error('No agent/session selected');
			await skillApi.add(agentId, sessionId, { skill_path: skillPath });
			await refetch();
		},
		[agentId, sessionId, refetch],
	);

	/** Removes a skill by name and refreshes the list. */
	const remove = useCallback(
		async (skillName: string) => {
			if (!agentId || !sessionId) throw new Error('No agent/session selected');
			await skillApi.remove(skillName, agentId, sessionId);
			await refetch();
		},
		[agentId, sessionId, refetch],
	);

	return { skills, loading, error, refetch, add, remove };
}
