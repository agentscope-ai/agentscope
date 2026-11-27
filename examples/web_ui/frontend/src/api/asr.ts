import { client, type ApiScope } from './client';
import type { RealtimeASRTokenResponse, TranscriptionResponse } from './types';

interface TranscriptionRequest {
	file: File;
	credentialId: string;
	model: string;
	parameters: Record<string, unknown>;
	signal?: AbortSignal;
	scope?: ApiScope;
}

export const asrApi = {
	createRealtimeToken: (credentialId: string, model: string, scope?: ApiScope) =>
		client.post<RealtimeASRTokenResponse>(
			'/asr/realtime/token',
			{ credential_id: credentialId, model },
			undefined,
			{ ...scope, silent: true, timeoutMs: 15_000 },
		),
	transcribe: ({
		file,
		credentialId,
		model,
		parameters,
		signal,
		scope,
	}: TranscriptionRequest) => {
		const body = new FormData();
		body.append('file', file);
		body.append('credential_id', credentialId);
		body.append('model', model);
		body.append('parameters', JSON.stringify(parameters));
		return client.post<TranscriptionResponse>('/asr/transcriptions', body, undefined, {
			...scope,
			silent: true,
			signal,
			timeoutMs: 60_000,
		});
	},
};
