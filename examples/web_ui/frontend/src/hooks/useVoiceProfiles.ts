import { useQuery } from '@tanstack/react-query';

import { voiceProfileApi } from '@/api';

export const VOICE_PROFILES_KEY = ['voice-profiles'];

export function useVoiceProfiles() {
	const { data, isPending, refetch } = useQuery({
		queryKey: VOICE_PROFILES_KEY,
		queryFn: () => voiceProfileApi.list(),
	});

	return { profiles: data?.profiles ?? [], loading: isPending, refetch };
}
