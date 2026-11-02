import { client } from './client';
import type {
	ListEmbeddingModelResponse,
	ListModelResponse,
	ListRealtimeModelResponse,
	ListTTSModelResponse,
} from './types';

export const modelApi = {
	list: (provider: string) => client.get<ListModelResponse>('/model/', { provider }),
};

export const ttsModelApi = {
	list: (provider: string) => client.get<ListTTSModelResponse>('/tts-model/', { provider }),
};

export const realtimeModelApi = {
	list: (provider: string) =>
		client.get<ListRealtimeModelResponse>('/realtime/models', { provider }),
};

export const embeddingModelApi = {
	list: (provider: string) =>
		client.get<ListEmbeddingModelResponse>('/embedding-model/', { provider }),
};
