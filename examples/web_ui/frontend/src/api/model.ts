import { client } from './client';
import type { ListModelResponse } from './types';

export const modelApi = {
	list: (provider: string) => client.get<ListModelResponse>('/model/', { provider }),
};
