/* eslint-disable react-refresh/only-export-components -- renderer constant is co-located with its inline component by design */
import { useState } from 'react';

import { CornerLine, ToolStateIcon } from './_shared';
import type { TFunction, ToolCallWithResult, ToolRenderer } from './types';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';

function parseInput(input: string): Record<string, unknown> {
	try {
		return JSON.parse(input);
	} catch {
		return {};
	}
}

/**
 * Collapsed: "TaskCreate  Created 3 tasks ..."
 * Expanded:
 *   TaskCreate  Created 3 tasks
 *     Research duck facts
 *       Find five interesting facts about ducks
 *     Write summary
 *       Compile the research into a brief summary
 *     Review code
 *       Check for bugs and style issues
 */
function TaskCreateGroup({ calls, t }: { calls: ToolCallWithResult[]; t: TFunction }) {
	const [open, setOpen] = useState(false);

	return (
		<Collapsible open={open} onOpenChange={setOpen} className="flex flex-col w-full">
			<CollapsibleTrigger className="flex flex-row gap-x-2 w-full max-w-full items-center cursor-pointer text-left">
				<ToolStateIcon states={calls.map((c) => c.result?.state)} />
				<span className="text-sm flex-1 min-w-0 truncate">
					<strong className="text-primary">{t('tool.taskCreate.label')}</strong>{' '}
					{t('tool.taskCreate.count', { count: calls.length })}
					{!open && <span className="text-muted-foreground"> ...</span>}
				</span>
			</CollapsibleTrigger>
			<CollapsibleContent className="pl-6 pt-2 flex flex-col gap-y-2 text-sm">
				{calls.map(({ call, result }) => {
					const input = parseInput(call.input);
					const subject = (input.subject as string) || '(untitled)';
					const description = (input.description as string) || '';
					// Extract the numeric task id from result text:
					// "Task (id=3) created successfully: ..."
					const resultText =
						typeof result?.output === 'string'
							? result.output
							: Array.isArray(result?.output)
								? result.output.map((b) => ('text' in b ? b.text : '')).join('')
								: '';
					const idMatch = resultText.match(/^Task \(id=(\d+)\)/);
					const taskId = idMatch ? idMatch[1] : null;
					return (
						<div key={call.id} className="flex flex-col min-w-0">
							<span className="text-xs break-all font-medium">
								{taskId && (
									<span className="text-muted-foreground font-mono">
										#{taskId}
									</span>
								)}
								{taskId && ' '}
								{subject}
							</span>
							{description && (
								<div className="flex flex-row gap-x-2 items-start pl-2 text-xs">
									<CornerLine />
									<span className="text-muted-foreground break-all">
										{description}
									</span>
								</div>
							)}
						</div>
					);
				})}
			</CollapsibleContent>
		</Collapsible>
	);
}

export const TaskCreateRenderer: ToolRenderer = {
	getDisplayName: (_call, t) => t('tool.taskCreate.name'),

	renderCallArgs: (call) => {
		const input = parseInput(call.input);
		return (input.subject as string) || '';
	},

	renderGroup: (calls, t) => <TaskCreateGroup calls={calls} t={t} />,
};
