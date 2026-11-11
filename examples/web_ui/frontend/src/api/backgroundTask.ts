import { client } from './client';
import type { ListBackgroundTasksResponse } from './types';

export const backgroundTaskApi = {
	list: (sessionId: string, baseUrl: string, userId: string, signal: AbortSignal) =>
		client.get<ListBackgroundTasksResponse>(
			'/background-tasks',
			{ session_id: sessionId },
			{ baseUrl, userId, signal, silent: true, timeoutMs: 10_000 },
		),
};
