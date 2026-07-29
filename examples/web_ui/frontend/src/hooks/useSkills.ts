import { useState, useEffect, useCallback } from 'react';

import { workspaceApi } from '../api';
import type { Skill } from '../api';
import type { UploadOptions } from '../api/workspace';

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
			setSkills(await workspaceApi.skill.list(agentId, sessionId));
		} catch (e) {
			setError(e as Error);
		} finally {
			setLoading(false);
		}
	}, [agentId, sessionId]);

	useEffect(() => {
		refetch();
	}, [refetch]);

	/** Uploads a picked folder as a skill and refreshes the list. */
	const upload = useCallback(
		async (files: File[], options: UploadOptions = {}) => {
			if (!agentId || !sessionId) throw new Error('No agent/session selected');
			await workspaceApi.skill.upload(agentId, sessionId, files, options);
			await refetch();
		},
		[agentId, sessionId, refetch],
	);

	/** Installs skills the user already has and refreshes the list. */
	const addFromLibrary = useCallback(
		async (skillIds: string[]) => {
			if (!agentId || !sessionId) throw new Error('No agent/session selected');
			const result = await workspaceApi.skill.addFromLibrary(agentId, sessionId, skillIds);
			await refetch();
			// Reported per skill, so a partial success is still a success
			// for what landed; surface only what did not.
			const failures = Object.entries(result.failed);
			if (failures.length > 0) {
				throw new Error(failures.map(([name, why]) => `${name}: ${why}`).join('\n'));
			}
		},
		[agentId, sessionId, refetch],
	);

	/** Removes a skill by name and refreshes the list. */
	const remove = useCallback(
		async (skillName: string) => {
			if (!agentId || !sessionId) throw new Error('No agent/session selected');
			await workspaceApi.skill.remove(skillName, agentId, sessionId);
			await refetch();
		},
		[agentId, sessionId, refetch],
	);

	return { skills, loading, error, refetch, upload, addFromLibrary, remove };
}
