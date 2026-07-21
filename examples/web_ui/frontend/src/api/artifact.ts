import { client } from './client';
import type { ListArtifactsResponse } from './types';

export const artifactApi = {
	list: async (agentId: string, sessionId: string, path = '.') => {
		const response = await client.get<ListArtifactsResponse>('/workspace/artifacts', {
			agent_id: agentId,
			session_id: sessionId,
			path,
		});
		return response.artifacts;
	},

	content: (agentId: string, sessionId: string, path: string) =>
		client.response('/workspace/artifacts/content', {
			agent_id: agentId,
			session_id: sessionId,
			path,
		}),
};
