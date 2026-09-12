import { getResultText, parseInput, toolArgClass, toolLabelClass } from './_shared';
import type { ToolRenderer } from './types';

function getPattern(input: string): string {
	const { pattern } = parseInput(input) as { pattern?: string };
	return pattern || input;
}

export const GlobRenderer: ToolRenderer = {
	getDisplayName: (_call, t) => t('tool.glob.name'),

	renderHeader: (pair) => (
		<>
			<span className={toolLabelClass}>Glob pattern</span>
			<span className={toolArgClass}>{getPattern(pair.call.input)}</span>
		</>
	),

	renderBody: (pair) =>
		pair.result ? (
			<pre className="border rounded-sm bg-background p-2 font-mono text-xs overflow-x-auto whitespace-pre">
				{getResultText(pair.result)}
			</pre>
		) : null,
};
