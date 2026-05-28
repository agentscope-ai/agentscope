import type { ToolResultBlock } from '@agentscope-ai/agentscope/message';
import { Circle } from 'lucide-react';
import type { ReactNode } from 'react';

import type { ToolCallWithResult } from './types';
import lineCornerSvg from '@/assets/images/line-corner.svg';
import lineVerticalSvg from '@/assets/images/line-vertical.svg';

/**
 * Pick the connector image for an item at `index` of `total`:
 * corner for the last row, vertical otherwise.
 */
function getLineImage(index: number, total: number): string {
	return index === total - 1 ? lineCornerSvg : lineVerticalSvg;
}

/**
 * Single tree-line cell. Use inside flex rows where one column is the line
 * and the next is the actual content.
 */
export function TreeLine({
	index,
	total,
	className = 'w-3 h-full',
}: {
	index: number;
	total: number;
	className?: string;
}) {
	return (
		<div className="flex-shrink-0 h-full items-center">
			<img src={getLineImage(index, total)} alt="" className={className} />
		</div>
	);
}

/**
 * Single corner-only line, used when only one trailing item exists.
 */
export function CornerLine({ className = 'w-3 h-4' }: { className?: string }) {
	return (
		<div className="flex-shrink-0">
			<img src={lineCornerSvg} alt="" className={className} />
		</div>
	);
}

/**
 * Aggregated state icon over a list of tool result states. Mirrors the
 * pre-refactor priority: running/undefined → pulsing muted; all success →
 * green; any error → red; any interrupted → yellow; otherwise muted.
 */
export function ToolStateIcon({ states }: { states: (ToolResultBlock['state'] | undefined)[] }) {
	if (states.includes('running') || states.includes(undefined)) {
		return (
			<Circle className="size-2.5 text-muted-foreground fill-muted-foreground animate-pulse shrink-0" />
		);
	}
	if (states.every((state) => state === 'success')) {
		return <Circle className="size-2.5 text-green-500 fill-green-500 shrink-0" />;
	}
	if (states.some((state) => state === 'error')) {
		return <Circle className="size-2.5 text-red-500 fill-red-500 shrink-0" />;
	}
	if (states.some((state) => state === 'interrupted')) {
		return <Circle className="size-2.5 text-yellow-500 fill-yellow-500 shrink-0" />;
	}
	return <Circle className="size-2.5 text-muted-foreground fill-muted-foreground shrink-0" />;
}

/**
 * Header + indented item list, used by Read / Glob / Grep group renderers.
 * Each item shows only the per-call args (no result), connected by SVG tree
 * lines. `inline` lays items horizontally instead of stacked.
 */
export function ToolCallGroupList({
	calls,
	label,
	renderItem,
	inline,
}: {
	calls: ToolCallWithResult[];
	label: ReactNode;
	renderItem: (item: ToolCallWithResult) => ReactNode;
	inline?: boolean;
}) {
	return (
		<div className="flex flex-col w-full">
			<div className="flex flex-row gap-x-2 w-full max-w-full items-center">
				<ToolStateIcon states={calls.map((item) => item.result?.state)} />
				{label}
			</div>
			<div className={`flex ${inline ? 'flex-row' : 'flex-col'} gap-x-2 pl-6 max-w-full`}>
				{calls.map((item, index) => (
					<div
						key={item.call.id}
						className="flex flex-row gap-x-2 w-full max-w-full items-stretch"
					>
						<TreeLine index={index} total={calls.length} />
						<div className="truncate flex-1 min-w-0 text-sm">{renderItem(item)}</div>
					</div>
				))}
			</div>
		</div>
	);
}
