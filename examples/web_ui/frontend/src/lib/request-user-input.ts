import type { ContentBlock } from '@agentscope-ai/agentscope/message';

import type { ClientExternalToolDefinition } from '@/api/types';

export const REQUEST_USER_INPUT_TOOL_NAME = 'client__request_user_input';
export const LEGACY_REQUEST_USER_INPUT_TOOL_NAME = 'RequestUserInput';

export const REQUEST_USER_INPUT_EXTERNAL_TOOL: ClientExternalToolDefinition = {
	name: REQUEST_USER_INPUT_TOOL_NAME,
	description:
		'Ask the user one question when their choice is required before continuing. ' +
		'Provide 2 to 4 mutually exclusive options with concise labels and optional ' +
		'descriptions. Mark at most one option as recommended. Do not add an Other ' +
		'option because the client always adds it and lets the user enter custom text.',
	read_only: true,
	input_schema: {
		type: 'object',
		properties: {
			question: {
				type: 'string',
				minLength: 1,
				maxLength: 500,
				description: 'The question the user must answer.',
			},
			options: {
				type: 'array',
				minItems: 2,
				maxItems: 4,
				description: 'Mutually exclusive choices. Do not include Other.',
				items: {
					type: 'object',
					properties: {
						label: {
							type: 'string',
							minLength: 1,
							maxLength: 80,
						},
						description: {
							type: 'string',
							maxLength: 300,
						},
						recommended: {
							type: 'boolean',
							default: false,
						},
					},
					required: ['label'],
					additionalProperties: false,
				},
			},
		},
		required: ['question', 'options'],
		additionalProperties: false,
	},
};

export type RequestUserInputResult =
	| { type: 'option'; option_index: number; label: string }
	| { type: 'other'; text: string };

export type RequestUserInputOption = {
	label: string;
	description?: string;
	recommended?: boolean;
};

export type RequestUserInputPayload = {
	question: string;
	options: RequestUserInputOption[];
};

const isRecord = (value: unknown): value is Record<string, unknown> => {
	return value !== null && typeof value === 'object' && !Array.isArray(value);
};

const isBoundedString = (value: unknown, minimum: number, maximum: number): value is string => {
	return typeof value === 'string' && value.length >= minimum && value.length <= maximum;
};

export const parseRequestUserInputPayload = (input: string): RequestUserInputPayload | null => {
	try {
		const payload: unknown = JSON.parse(input);
		if (!isRecord(payload) || !isBoundedString(payload.question, 1, 500)) return null;
		if (!Array.isArray(payload.options) || payload.options.length < 2) return null;
		if (payload.options.length > 4) return null;

		const options: RequestUserInputOption[] = [];
		for (const value of payload.options) {
			if (!isRecord(value) || !isBoundedString(value.label, 1, 80)) return null;
			if (value.description !== undefined && !isBoundedString(value.description, 0, 300)) {
				return null;
			}
			if (value.recommended !== undefined && typeof value.recommended !== 'boolean') {
				return null;
			}
			options.push({
				label: value.label,
				...(value.description === undefined ? {} : { description: value.description }),
				...(value.recommended === undefined ? {} : { recommended: value.recommended }),
			});
		}

		return { question: payload.question, options };
	} catch {
		return null;
	}
};

export const parseRequestUserInputResult = (input: string): RequestUserInputResult | null => {
	try {
		const result: unknown = JSON.parse(input);
		if (!isRecord(result)) return null;
		if (result.type === 'other' && typeof result.text === 'string') {
			return result.text.trim() ? { type: 'other', text: result.text.trim() } : null;
		}
		if (
			result.type === 'option' &&
			Number.isInteger(result.option_index) &&
			typeof result.option_index === 'number' &&
			result.option_index >= 0 &&
			typeof result.label === 'string'
		) {
			return {
				type: 'option',
				option_index: result.option_index,
				label: result.label,
			};
		}
		return null;
	} catch {
		return null;
	}
};

export const getInitialRadioIndex = (options: Array<{ recommended?: boolean }>): number | null => {
	if (options.length === 0) return null;
	const recommendedIndex = options.findIndex((option) => option.recommended);
	return recommendedIndex >= 0 ? recommendedIndex : 0;
};

export const getRadioEnterAction = (
	currentIndex: number,
	optionCount: number,
	otherText: string,
): 'submit' | 'edit-other' => {
	return currentIndex === optionCount && otherText.trim().length === 0 ? 'edit-other' : 'submit';
};

export const shouldSubmitOtherInput = (
	key: string,
	shiftKey: boolean,
	isComposing: boolean,
): boolean => {
	return key === 'Enter' && !shiftKey && !isComposing;
};

export const getNextRadioIndex = (
	currentIndex: number,
	key: string,
	itemCount: number,
): number | null => {
	if (itemCount <= 0) return null;

	const index = currentIndex >= 0 && currentIndex < itemCount ? currentIndex : 0;
	switch (key) {
		case 'ArrowDown':
		case 'ArrowRight':
			return (index + 1) % itemCount;
		case 'ArrowUp':
		case 'ArrowLeft':
			return (index - 1 + itemCount) % itemCount;
		case 'Home':
			return 0;
		case 'End':
			return itemCount - 1;
		default:
			return null;
	}
};

/** Convert the composer's single plain-text block into an Other answer. */
export const toOtherUserInputResult = (blocks: ContentBlock[]): RequestUserInputResult | null => {
	if (blocks.length !== 1 || blocks[0].type !== 'text') return null;
	const text = blocks[0].text.trim();
	return text ? { type: 'other', text } : null;
};
