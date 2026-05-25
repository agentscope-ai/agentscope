import { client } from './client';
import type {
	CreateSessionRequest,
	CreateSessionResponse,
	SessionListResponse,
	SessionRecord,
	UpdateSessionRequest,
	Msg,
} from './types';

export interface MessagesResponse {
	messages: Msg[];
	is_running: boolean;
}

export const sessionApi = {
	list: (agentId: string) => client.get<SessionListResponse>('/sessions/', { agent_id: agentId }),

	create: (body: CreateSessionRequest) => client.post<CreateSessionResponse>('/sessions/', body),

	update: (sessionId: string, agentId: string, body: UpdateSessionRequest) =>
		client.patch<SessionRecord>(`/sessions/${sessionId}`, body, { agent_id: agentId }),

	delete: (sessionId: string, agentId: string) =>
		client.delete(`/sessions/${sessionId}`, { agent_id: agentId }),

	messages: (sessionId: string, agentId: string, offset = 0, limit = 50) =>
		client.get<MessagesResponse>(`/sessions/${sessionId}/messages`, {
			agent_id: agentId,
			offset: String(offset),
			limit: String(limit),
		}),
};
