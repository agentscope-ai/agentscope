import { client } from './client';
import type {
	ChannelChatIdsResponse,
	ChannelRecord,
	ChannelSessionsResponse,
	ChannelStatus,
	ChannelTypeSchema,
	CreateChannelRequest,
	UpdateChannelRequest,
} from './types';

export const channelApi = {
	listTypes: () => client.get<ChannelTypeSchema[]>('/channels/types'),

	list: () => client.get<ChannelRecord[]>('/channels/'),

	get: (channelId: string) => client.get<ChannelRecord>(`/channels/${channelId}`),

	create: (body: CreateChannelRequest) => client.post<ChannelRecord>('/channels/', body),

	update: (channelId: string, body: UpdateChannelRequest) =>
		client.patch<ChannelRecord>(`/channels/${channelId}`, body),

	delete: (channelId: string) => client.delete(`/channels/${channelId}`),

	enable: (channelId: string) => client.post<{ status: string }>(`/channels/${channelId}/enable`),

	disable: (channelId: string) =>
		client.post<{ status: string }>(`/channels/${channelId}/disable`),

	status: (channelId: string) => client.get<ChannelStatus>(`/channels/${channelId}/status`),

	listSessions: (channelId: string) =>
		client.get<ChannelSessionsResponse>(`/channels/${channelId}/sessions`),

	listChatIds: (channelId: string) =>
		client.get<ChannelChatIdsResponse>(`/channels/${channelId}/chat_ids`),
};
