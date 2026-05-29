/* eslint-disable react-refresh/only-export-components -- renderer constant is co-located with its inline component by design */
import type { ToolResultBlock } from '@agentscope-ai/agentscope/message';
import { useState } from 'react';

import { CornerLine, ToolStateIcon } from './_shared';
import type { TFunction, ToolCallWithResult, ToolRenderer } from './types';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { formatNumber } from '@/utils/common';

function parseInput(input: string): Record<string, unknown> {
	try {
		return JSON.parse(input);
	} catch {
		return {};
	}
}

function getFilePath(input: string): string {
	const { file_path } = parseInput(input) as { file_path?: string };
	return file_path || input;
}

/** Count lines in a tool result's output string, including text content from
 * non-text blocks. Returns 0 when the result is missing or empty. */
function countResultLines(result?: ToolResultBlock): number {
	if (!result) return 0;
	let str: string;
	if (typeof result.output === 'string') {
		str = result.output;
	} else {
		str = result.output.map((b) => (b.type === 'text' ? b.text : '')).join('\n');
	}
	if (!str) return 0;
	return str.split('\n').length;
}

/** Collapse consecutive Read calls of the same `file_path` into one bucket so
 * the path is shown once, followed by one corner-row per call. Order is
 * preserved; a different path or a re-occurrence after another path starts a
 * new bucket. */
function groupByConsecutivePath(
	calls: ToolCallWithResult[],
): Array<{ path: string; calls: ToolCallWithResult[] }> {
	const groups: Array<{ path: string; calls: ToolCallWithResult[] }> = [];
	for (const item of calls) {
		const path = getFilePath(item.call.input);
		const last = groups[groups.length - 1];
		if (last && last.path === path) {
			last.calls.push(item);
		} else {
			groups.push({ path, calls: [item] });
		}
	}
	return groups;
}

function ReadGroup({ calls, t }: { calls: ToolCallWithResult[]; t: TFunction }) {
	const [open, setOpen] = useState(false);
	const name = t('tool.read.name');

	return (
		<Collapsible open={open} onOpenChange={setOpen} className="flex flex-col w-full ">
			<CollapsibleTrigger className="flex flex-row gap-x-2 w-full max-w-full items-center cursor-pointer text-left">
				<ToolStateIcon states={calls.map((c) => c.result?.state)} />
				<span className="text-sm flex-1 min-w-0 truncate">
					<strong className="text-primary">{name} </strong>
					{t('tool.read.fileCount', { count: calls.length })}
					{!open && <span className="text-muted-foreground"> ...</span>}
				</span>
				{/*<ChevronRight*/}
				{/*	className={`size-4 shrink-0 transition-transform ${open ? 'rotate-90' : ''}`}*/}
				{/*/>*/}
			</CollapsibleTrigger>
			<CollapsibleContent className="pl-6 pt-2 flex flex-col gap-y-2 text-sm">
				{groupByConsecutivePath(calls).map((group, gIdx) => (
					<div key={gIdx} className="flex flex-col min-w-0">
						<div className="truncate">
							{/*<strong className="text-primary">{name}</strong>*/}
							{/*<span className="text-muted-foreground">({group.path})</span>*/}
							<span
								className={
									'text-xs !overflow-visible !whitespace-normal !text-clip break-all'
								}
							>
								{group.path}
							</span>
						</div>
						{group.calls.map(({ call, result }) => {
							if (!result) return null;
							const lines = countResultLines(result);
							return (
								<div
									key={call.id}
									className="flex flex-row gap-x-2 items-center pl-2 text-xs"
								>
									<CornerLine />
									<span className="text-muted-foreground">
										{t('tool.read.lineCount', {
											count: lines,
											formatted: formatNumber(lines),
										})}
									</span>
								</div>
							);
						})}
					</div>
				))}
			</CollapsibleContent>
		</Collapsible>
	);
}

export const ReadRenderer: ToolRenderer = {
	getDisplayName: (_call, t) => t('tool.read.name'),

	renderCallArgs: (call) => getFilePath(call.input),

	renderConfirmBody: (call) => (
		<div className="w-full max-w-full overflow-hidden text-ellipsis truncate">
			<div className="text-secondary-foreground">{getFilePath(call.input)}</div>
		</div>
	),

	renderGroup: (calls, t) => <ReadGroup calls={calls} t={t} />,
};
