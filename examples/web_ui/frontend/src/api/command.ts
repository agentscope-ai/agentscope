import { client } from './client';
import type { CommandListResponse } from './types';

export const commandApi = {
	list: () => client.get<CommandListResponse>('/commands/'),
};
