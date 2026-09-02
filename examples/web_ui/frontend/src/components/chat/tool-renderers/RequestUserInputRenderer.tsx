import { getResultText, toolArgClass, toolLabelClass } from './_shared';
import { defaultRenderBody } from './DefaultRenderer';
import type { TFunction, ToolCallWithResult, ToolRenderer } from './types';
import {
	parseRequestUserInputPayload,
	parseRequestUserInputResult,
} from '@/lib/request-user-input';

const getAnswer = (pair: ToolCallWithResult, t: TFunction): string | null => {
	const result = parseRequestUserInputResult(getResultText(pair.result));
	if (!result) return null;
	return result.type === 'option'
		? result.label
		: `${t('requestUserInput.other')}: ${result.text}`;
};

export const RequestUserInputRenderer: ToolRenderer = {
	renderHeader(pair, t) {
		const answer = getAnswer(pair, t);
		const payload = parseRequestUserInputPayload(pair.call.input);
		return (
			<>
				<span className={toolLabelClass}>
					{t(
						answer
							? 'requestUserInput.historyAnswered'
							: 'requestUserInput.historyAsked',
					)}
				</span>
				<span className={toolArgClass}>
					{answer ?? payload?.question ?? pair.call.name}
				</span>
			</>
		);
	},
	renderBody(pair, t) {
		if (!pair.result) return null;
		const answer = getAnswer(pair, t);
		if (!answer) return defaultRenderBody(pair, t);
		const payload = parseRequestUserInputPayload(pair.call.input);
		return (
			<div className="rounded-sm border bg-background px-3 py-2">
				{payload ? (
					<p className="text-xs leading-5 text-muted-foreground">{payload.question}</p>
				) : null}
				<p className="mt-0.5 break-words text-sm text-foreground">{answer}</p>
			</div>
		);
	},
};
