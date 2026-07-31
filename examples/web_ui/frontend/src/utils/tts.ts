/**
 * TTS utility functions shared across voice-profile and popover.
 */

const ENGINE_CREDENTIAL_TYPE: Record<string, string> = {
	cosyvoice: 'dashscope_credential',
	dashscope_tts: 'dashscope_credential',
	openai_tts: 'openai_credential',
	gemini_tts: 'gemini_credential',
};

/** Return the credential provider type required by a TTS engine. */
export function credentialTypeForEngine(engine: string): string | undefined {
	return ENGINE_CREDENTIAL_TYPE[engine];
}

/**
 * Check whether a TTS model name belongs to a given engine.
 * This is the single source of truth for engine → model prefix matching.
 */
export function isModelForEngine(modelName: string, engine: string): boolean {
	switch (engine) {
		case 'dashscope_tts':
			return modelName.startsWith('qwen');
		case 'cosyvoice':
			return modelName.startsWith('cosyvoice');
		case 'openai_tts':
			return modelName.startsWith('tts-') || modelName.includes('tts');
		case 'gemini_tts':
			return modelName.includes('gemini');
		default:
			return modelName === engine || modelName.includes(engine);
	}
}
