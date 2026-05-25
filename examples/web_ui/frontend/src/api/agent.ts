import { client } from './client';
import type {
	AgentListResponse,
	AgentRecord,
	CreateAgentRequest,
	CreateAgentResponse,
	UpdateAgentRequest,
} from './types';

export const agentApi = {
	list: () => client.get<AgentListResponse>('/agent/'),

	create: (body: CreateAgentRequest) => client.post<CreateAgentResponse>('/agent/', body),

	update: (agentId: string, body: UpdateAgentRequest) =>
		client.patch<AgentRecord>(`/agent/${agentId}`, body),

	delete: (agentId: string) => client.delete(`/agent/${agentId}`),
};
