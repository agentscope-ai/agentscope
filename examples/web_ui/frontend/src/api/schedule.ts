import { client } from './client';
import type {
	CreateScheduleRequest,
	CreateScheduleResponse,
	ScheduleListResponse,
	ScheduleRecord,
	ScheduleSessionsResponse,
	UpdateScheduleRequest,
} from './types';

export const scheduleApi = {
	list: () => client.get<ScheduleListResponse>('/schedule/'),

	create: (body: CreateScheduleRequest) =>
		client.post<CreateScheduleResponse>('/schedule/', body),

	update: (scheduleId: string, body: UpdateScheduleRequest) =>
		client.patch<ScheduleRecord>(`/schedule/${scheduleId}`, body),

	delete: (scheduleId: string) => client.delete(`/schedule/${scheduleId}`),

	listSessions: (scheduleId: string) =>
		client.get<ScheduleSessionsResponse>(`/schedule/${scheduleId}/sessions`),
};
