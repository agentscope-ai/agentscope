import { client } from './client';
import type {
	ChannelRecord,
	ChannelStatusResponse,
	ChannelTypeSchema,
	CreateChannelRequest,
	RoutingRule,
	UpdateChannelRequest,
} from './types';

export interface BindingResponse {
	id: number;
	metadata_key: string;
	metadata_value: string;
	agent_id: string;
	priority: number;
}

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

	status: (channelId: string) =>
		client.get<ChannelStatusResponse>(`/channels/${channelId}/status`),

	test: (channelId: string) =>
		client.post<{ status: string; message: string }>(`/channels/${channelId}/test`),

	listBindings: (channelId: string) =>
		client.get<BindingResponse[]>(`/channels/${channelId}/bindings`),

	addBinding: (channelId: string, rule: Omit<RoutingRule, 'priority'> & { priority?: number }) =>
		client.post<BindingResponse>(`/channels/${channelId}/bindings`, rule),

	deleteBinding: (channelId: string, bindingIdx: number) =>
		client.delete(`/channels/${channelId}/bindings/${bindingIdx}`),

	listChatIds: (channelId: string) =>
		client.get<{ chat_id: string; name: string; source: string }[]>(
			`/channels/${channelId}/chat_ids`,
		),
};
