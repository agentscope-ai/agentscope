import { useQuery } from '@tanstack/react-query';

import { voiceProfileApi } from '@/api';
import type { EngineInfo } from '@/api';

const EMPTY_ENGINES: EngineInfo[] = [];

/** Share engine capabilities and owned credentials across voice controls. */
export function useVoiceEngines(enabled = true) {
	const { data, isFetching, isSuccess, refetch } = useQuery({
		queryKey: ['voice-engines'],
		queryFn: () => voiceProfileApi.availableEngines(),
		enabled,
		staleTime: 30_000,
		retry: false,
	});

	return {
		engineDetails: data?.engine_details ?? EMPTY_ENGINES,
		loading: isFetching,
		ready: isSuccess,
		refetch,
	};
}
