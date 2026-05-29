import { client } from './client';
import type {
	AgentListResponse,
	AgentRecord,
	AgentSchemaResponse,
	CreateAgentRequest,
	CreateAgentResponse,
	UpdateAgentRequest,
} from './types';

export const agentApi = {
	list: () => client.get<AgentListResponse>('/agent/'),

	getSchema: () => client.get<AgentSchemaResponse>('/agent/schema'),

	create: (body: CreateAgentRequest) => client.post<CreateAgentResponse>('/agent/', body),

	update: (agentId: string, body: UpdateAgentRequest) =>
		client.patch<AgentRecord>(`/agent/${agentId}`, body),

	delete: (agentId: string) => client.delete(`/agent/${agentId}`),
};
