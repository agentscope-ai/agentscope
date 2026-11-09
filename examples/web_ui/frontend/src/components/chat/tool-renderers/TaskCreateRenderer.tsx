import { getResultText, parseInput, toolArgClass, toolLabelClass } from './_shared';
import type { ToolCallWithResult, ToolRenderer } from './types';

/**
 * Extract the numeric task id the backend echoes in its result text
 * (`"Task (id=3) created successfully: ..."`), or `null` if absent.
 */
function getTaskId(pair: ToolCallWithResult): string | null {
	const match = getResultText(pair.result).match(/^Task \(id=(\d+)\)/);
	return match ? match[1] : null;
}

export const TaskCreateRenderer: ToolRenderer = {
	getDisplayName: (_call, t) => t('tool.taskCreate.name'),

	// Trigger line: "Create task #{id} {subject}"
	renderHeader: (pair) => {
		const subject = (parseInput(pair.call.input).subject as string) || '(untitled)';
		const taskId = getTaskId(pair);
		return (
			<>
				<span className={toolLabelClass}>Create task</span>
				<span className={toolArgClass}>
					{taskId && <span className="text-muted-foreground font-mono">#{taskId} </span>}
					{subject}
				</span>
			</>
		);
	},

	// Expanded body: the task description.
	renderBody: (pair) => {
		const description = (parseInput(pair.call.input).description as string) || '';
		return description ? (
			<div className="border rounded-sm bg-background p-2 text-xs text-muted-foreground break-all">
				{description}
			</div>
		) : null;
	},
};
