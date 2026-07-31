import { client } from './client';
import type { MCPClient, MCPClientStatus, Skill } from './types';

export const workspaceApi = {
	mcp: {
		list: (agentId: string, sessionId: string) =>
			client.get<MCPClientStatus[]>('/workspace/mcp', {
				agent_id: agentId,
				session_id: sessionId,
			}),

		add: (agentId: string, sessionId: string, mcp: MCPClient) =>
			client.post<void>('/workspace/mcp', mcp, { agent_id: agentId, session_id: sessionId }),

		remove: (mcpName: string, agentId: string, sessionId: string) =>
			client.delete(`/workspace/mcp/${mcpName}`, {
				agent_id: agentId,
				session_id: sessionId,
			}),
	},

	skill: {
		list: (agentId: string, sessionId: string) =>
			client.get<Skill[]>('/workspace/skill', { agent_id: agentId, session_id: sessionId }),

		add: (agentId: string, sessionId: string, files: File[]) => {
			const body = new FormData();
			files.forEach((file) => {
				body.append('files', file, file.webkitRelativePath || file.name);
			});
			return client.post<void>('/workspace/skill', body, {
				agent_id: agentId,
				session_id: sessionId,
			});
		},

		remove: (skillName: string, agentId: string, sessionId: string) =>
			client.delete(`/workspace/skill/${skillName}`, {
				agent_id: agentId,
				session_id: sessionId,
			}),
	},
};
