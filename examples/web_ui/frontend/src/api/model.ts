import { client } from './client';
import type {
	ListASRModelResponse,
	ListEmbeddingModelResponse,
	ListModelResponse,
	ListRealtimeModelResponse,
	ListTTSModelResponse,
} from './types';

export const asrModelApi = {
	list: (provider: string) => client.get<ListASRModelResponse>('/asr-model/', { provider }),
};

export const modelApi = {
	list: (provider: string, credentialId?: string) =>
		client.get<ListModelResponse>('/model/', {
			provider,
			...(credentialId ? { credential_id: credentialId } : {}),
		}),
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
