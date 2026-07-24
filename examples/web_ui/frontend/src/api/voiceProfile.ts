import { client } from './client';

export interface VoiceProfileData {
	name: string;
	engine?: string | null;
	model?: string | null;
	credential_id?: string | null;
	source?: 'api' | 'local' | null;
	voice?: string | null;
	metadata?: Record<string, unknown> | null;
}

export interface VoiceProfileRecord {
	id: string;
	user_id: string;
	created_at: string;
	updated_at: string;
	data: VoiceProfileData;
}

export interface ListVoiceProfilesResponse {
	profiles: VoiceProfileRecord[];
	total: number;
}

export interface CreateVoiceProfileResponse {
	profile_id: string;
}

export interface CredentialRef {
	id: string;
	label: string;
}

export interface EngineInfo {
	name: string;
	source: 'api' | 'local';
	gpu_requirement?: string | null;
	voice_cloning: boolean;
	credentials: CredentialRef[];
}

export interface AvailableEnginesResponse {
	engines: string[];
	engine_details: EngineInfo[];
}

export interface CloneVoiceRequest {
	engine: string;
	model?: string | null;
	audio_base64?: string | null;
	audio_filename?: string;
	audio_url?: string | null;
	text?: string | null;
	prefix?: string;
	consent?: string | null;
}

export interface CloneVoiceResponse {
	voice_id: string;
	credential_id: string;
}

export interface OpenAIConsentRequest {
	name?: string;
	language?: string;
	audio_base64: string;
	audio_filename?: string;
}

export interface OpenAIConsentResponse {
	consent_id: string;
}

export const voiceProfileApi = {
	list: () => client.get<ListVoiceProfilesResponse>('/voice-profile/'),

	get: (profileId: string) => client.get<VoiceProfileRecord>(`/voice-profile/${profileId}`),

	create: (data: VoiceProfileData) =>
		client.post<CreateVoiceProfileResponse>('/voice-profile/', { data }),

	update: (profileId: string, data: VoiceProfileData) =>
		client.patch<VoiceProfileRecord>(`/voice-profile/${profileId}`, { data }),

	delete: (profileId: string) => client.delete(`/voice-profile/${profileId}`),

	availableEngines: () =>
		client.get<AvailableEnginesResponse>('/voice-profile/available-engines'),

	cloneVoice: (data: CloneVoiceRequest) =>
		client.post<CloneVoiceResponse>('/voice-profile/clone', data),

	uploadOpenAIConsent: (data: OpenAIConsentRequest) =>
		client.post<OpenAIConsentResponse>('/voice-profile/openai-consent', data),
};
