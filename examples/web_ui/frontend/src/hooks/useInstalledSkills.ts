import { useCallback, useEffect, useState } from 'react';

import { skillApi } from '@/api';
import type { SkillView } from '@/api';

/**
 * The user's library of installed skills.
 *
 * Named apart from `useSkills`, which lists the skills present in one
 * session's workspace — these are user-level and need no agent/session.
 */
export function useInstalledSkills() {
	const [skills, setSkills] = useState<SkillView[]>([]);
	// Starts true: the first paint happens before the effect fires, and
	// a false start would flash the empty state before the spinner.
	const [loading, setLoading] = useState(true);
	const [error, setError] = useState<Error | null>(null);

	const refetch = useCallback(async () => {
		setLoading(true);
		setError(null);
		try {
			setSkills(await skillApi.list());
		} catch (e) {
			setError(e as Error);
		} finally {
			setLoading(false);
		}
	}, []);

	useEffect(() => {
		refetch();
	}, [refetch]);

	const remove = useCallback(
		async (skillId: string) => {
			await skillApi.remove(skillId);
			await refetch();
		},
		[refetch],
	);

	return { skills, loading, error, refetch, remove };
}
