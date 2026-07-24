import { useCallback, useEffect, useState } from 'react';

import { voiceProfileApi } from '@/api';
import type { VoiceProfileRecord } from '@/api';

export function useVoiceProfiles() {
	const [profiles, setProfiles] = useState<VoiceProfileRecord[]>([]);
	const [loading, setLoading] = useState(true);

	const refetch = useCallback(async () => {
		try {
			const res = await voiceProfileApi.list();
			setProfiles(res.profiles);
		} catch {
			setProfiles([]);
		} finally {
			setLoading(false);
		}
	}, []);

	useEffect(() => {
		refetch();
	}, [refetch]);

	return { profiles, loading, refetch };
}
