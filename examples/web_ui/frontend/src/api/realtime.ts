import { client } from './client';
import type { RealtimeConfigResponse, RealtimeOfferRequest, RealtimeOfferResponse } from './types';

export const realtimeApi = {
	config: () =>
		client.get<RealtimeConfigResponse>('/realtime/config', undefined, {
			timeoutMs: 10_000,
		}),
	offer: (sessionId: string, body: RealtimeOfferRequest) =>
		client.post<RealtimeOfferResponse>(
			`/realtime/sessions/${sessionId}/offer`,
			body,
			undefined,
			{ timeoutMs: 20_000 },
		),
};
