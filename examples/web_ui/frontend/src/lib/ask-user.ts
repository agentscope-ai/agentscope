import type { ContentBlock } from '@agentscope-ai/agentscope/message';

export const ASK_USER_TOOL_NAME = 'AskUser';

export type AskUserQuestion = {
	question: string;
	header: string;
	context?: string | null;
	options: { label: string; description: string; preview?: string | null }[];
	multi_select?: boolean;
};
export type AskUserAnswer = { question: string; selected: string[]; other: string | null };
export type AskUserResult = { answers: AskUserAnswer[] };

const record = (value: unknown): value is Record<string, unknown> =>
	typeof value === 'object' && value !== null && !Array.isArray(value);
const optionalText = (value: unknown) => value == null || typeof value === 'string';

export function parseAskUserPayload(input: string): { questions: AskUserQuestion[] } | null {
	try {
		const value: unknown = JSON.parse(input);
		if (!record(value) || !Array.isArray(value.questions)) return null;
		if (value.questions.length < 1 || value.questions.length > 4) return null;
		for (const question of value.questions) {
			if (
				!record(question) ||
				typeof question.question !== 'string' ||
				typeof question.header !== 'string' ||
				question.header.length > 12 ||
				!optionalText(question.context) ||
				(question.multi_select !== undefined &&
					typeof question.multi_select !== 'boolean') ||
				!Array.isArray(question.options) ||
				question.options.length < 2 ||
				question.options.length > 4
			)
				return null;
			if (
				!question.options.every(
					(option: unknown) =>
						record(option) &&
						typeof option.label === 'string' &&
						typeof option.description === 'string' &&
						optionalText(option.preview),
				)
			)
				return null;
			if (
				new Set(question.options.map((option) => option.label)).size !==
				question.options.length
			)
				return null;
		}
		if (
			new Set(value.questions.map((question) => question.question)).size !==
			value.questions.length
		)
			return null;
		return value as { questions: AskUserQuestion[] };
	} catch {
		return null;
	}
}

export function parseAskUserResult(value: unknown): AskUserResult | null {
	if (!record(value) || !Array.isArray(value.answers)) return null;
	if (
		!value.answers.every(
			(answer: unknown) =>
				record(answer) &&
				typeof answer.question === 'string' &&
				Array.isArray(answer.selected) &&
				answer.selected.every((label: unknown) => typeof label === 'string') &&
				optionalText(answer.other),
		)
	)
		return null;
	return {
		answers: value.answers.map((answer) => ({
			question: answer.question,
			selected: answer.selected,
			other: answer.other ?? null,
		})),
	};
}

export function formatAskUserOutput(result: AskUserResult): string {
	return result.answers
		.map(
			(answer) =>
				`${answer.question}\n${[...answer.selected, answer.other].filter(Boolean).join(', ')}`,
		)
		.join('\n\n');
}

export function toAskUserComposerResult(
	blocks: ContentBlock[],
	input: string,
): AskUserResult | null {
	const payload = parseAskUserPayload(input);
	if (!payload || payload.questions.length !== 1 || blocks.some((block) => block.type !== 'text'))
		return null;
	const text = blocks
		.filter((block) => block.type === 'text')
		.map((block) => block.text)
		.join('\n')
		.trim();
	return text
		? { answers: [{ question: payload.questions[0].question, selected: [], other: text }] }
		: null;
}

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
