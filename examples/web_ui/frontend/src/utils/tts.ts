/**
 * TTS utility functions shared across voice-profile and popover.
 */

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
