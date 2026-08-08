import { client } from './client';
import type { ListEmbeddingModelResponse, ListModelResponse, ListTTSModelResponse } from './types';

export const modelApi = {
	list: (provider: string) => client.get<ListModelResponse>('/model/', { provider }),
};

export const ttsModelApi = {
	list: (provider: string) => client.get<ListTTSModelResponse>('/tts-model/', { provider }),
};

export const embeddingModelApi = {
	list: (provider: string) =>
		client.get<ListEmbeddingModelResponse>('/embedding-model/', { provider }),
};
