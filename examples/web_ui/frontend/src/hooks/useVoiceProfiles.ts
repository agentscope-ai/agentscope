import { useQuery } from '@tanstack/react-query';

import { voiceProfileApi } from '@/api';
import { queryClient } from '@/lib/query-client';

const VOICE_PROFILES_KEY = ['voice-profiles'];
const STORAGE_KEY = 'agentscope:voice-profiles-changed';

function invalidateVoiceProfiles() {
	return queryClient.invalidateQueries({ queryKey: VOICE_PROFILES_KEY });
}

export function notifyVoiceProfilesChanged() {
	try {
		// Each write needs a distinct value to trigger a storage event.
		localStorage.setItem(STORAGE_KEY, Math.random().toString(36));
	} catch {
		// The current tab can still refresh when storage is unavailable.
	}
	return invalidateVoiceProfiles();
}

export function subscribeToVoiceProfileChanges() {
	const handleStorage = (event: StorageEvent) => {
		if (event.key === STORAGE_KEY) void invalidateVoiceProfiles();
	};
	window.addEventListener('storage', handleStorage);
	return () => window.removeEventListener('storage', handleStorage);
}

export function useVoiceProfiles(enabled = true) {
	const { data, isPending, refetch } = useQuery({
		queryKey: VOICE_PROFILES_KEY,
		queryFn: () => voiceProfileApi.list(),
		enabled,
	});

	return { profiles: data?.profiles ?? [], loading: isPending, refetch };
}
