import { ToolCallGroupList } from './_shared';
import type { ToolRenderer } from './types';

function parseInput(input: string): Record<string, unknown> {
	try {
		return JSON.parse(input);
	} catch {
		return {};
	}
}

function getPattern(input: string): string {
	const { pattern } = parseInput(input) as { pattern?: string };
	return pattern || input;
}

export const GlobRenderer: ToolRenderer = {
	getDisplayName: (_call, t) => t('tool.glob.name'),

	renderCallArgs: (call) => getPattern(call.input),

	renderGroup: (calls, t) => (
		<ToolCallGroupList
			calls={calls}
			inline
			label={<strong className="truncate text-primary text-sm">{t('tool.glob.name')}</strong>}
			renderItem={(item) => getPattern(item.call.input)}
		/>
	),
};
