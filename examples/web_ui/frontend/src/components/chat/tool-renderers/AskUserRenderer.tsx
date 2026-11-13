import { toolArgClass, toolLabelClass } from './_shared';
import { defaultRenderBody } from './DefaultRenderer';
import type { ToolRenderer } from './types';
import { parseAskUserPayload, parseAskUserResult } from '@/lib/ask-user';

export const AskUserRenderer: ToolRenderer = {
	renderHeader(pair, t) {
		const result = parseAskUserResult(pair.result?.metadata);
		const payload = parseAskUserPayload(pair.call.input);
		return (
			<>
				<span className={toolLabelClass}>
					{t(result ? 'askUser.historyAnswered' : 'askUser.historyAsked')}
				</span>
				<span className={toolArgClass}>
					{result?.answers
						.map((answer) =>
							[...answer.selected, answer.other].filter(Boolean).join(', '),
						)
						.join('; ') ||
						payload?.questions.map((question) => question.question).join('; ') ||
						pair.call.name}
				</span>
			</>
		);
	},
	renderBody(pair, t) {
		const result = parseAskUserResult(pair.result?.metadata);
		if (!result) return pair.result ? defaultRenderBody(pair, t) : null;
		return (
			<div className="space-y-3 rounded-lg border bg-background p-3">
				{result.answers.map((answer) => (
					<div key={answer.question}>
						<p className="text-xs text-muted-foreground">{answer.question}</p>
						<p className="mt-1 whitespace-pre-wrap break-words text-sm">
							{[...answer.selected, answer.other].filter(Boolean).join(', ')}
						</p>
					</div>
				))}
			</div>
		);
	},
};
