import type { ToolResultBlock } from '@agentscope-ai/agentscope/message';
import { Circle, Minus, Plus } from 'lucide-react';
import type { ReactNode } from 'react';

import type { ToolCallWithResult } from './types';
import lineCornerSvg from '@/assets/images/line-corner.svg';
import lineVerticalSvg from '@/assets/images/line-vertical.svg';
import { formatNumber } from '@/utils/common.ts';

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

/**
 * Parse the input arguments from the given string.
 * @param input
 * @returns The JSON Record or empty object if parsing fails.
 */
export function parseInput(input: string): Record<string, unknown> {
	try {
		return JSON.parse(input);
	} catch {
		return {};
	}
}

/**
 * Get the filepath from the input arguments
 * @param input
 * @returns The filepath from the input string
 */
export function getFilePath(input: string): string {
	const { file_path } = parseInput(input) as { file_path?: string };
	return file_path || input;
}

/**
 * Get the filename
 * @param input
 * @returns The filename from the input string, considering different OS path separators.
 */
export function getFileName(input: string): string {
	const filePath = getFilePath(input);
	const segments = filePath.split(/[/\\]+/).filter(Boolean);
	return segments.length > 0 ? segments[segments.length - 1] : filePath;
}

/**
 * Like ``getFileName`` but returns ``undefined`` when the input is not yet
 * a fully parseable JSON object with a non-empty ``file_path`` field. Use
 * this in ``renderCallArgs`` so partial JSON streamed during tool-call
 * generation doesn't render a garbled file name (e.g. a fragment of the
 * ``content`` field being mistaken for the path).
 */
export function tryGetFileName(input: string): string | undefined {
	let parsed: unknown;
	try {
		parsed = JSON.parse(input);
	} catch {
		return undefined;
	}
	if (!parsed || typeof parsed !== 'object') return undefined;
	const filePath = (parsed as { file_path?: unknown }).file_path;
	if (typeof filePath !== 'string' || filePath.length === 0) return undefined;
	const segments = filePath.split(/[/\\]+/).filter(Boolean);
	return segments.length > 0 ? segments[segments.length - 1] : filePath;
}

/**
 * Tally inserted / deleted lines from a unified diff text. The leading
 * ``+++`` / ``---`` lines (file headers) are excluded.
 */
export function countDiffStats(diffText: string): {
	insertions: number;
	deletions: number;
} {
	let insertions = 0;
	let deletions = 0;
	for (const line of diffText.split('\n')) {
		if (line.startsWith('+') && !line.startsWith('+++')) insertions++;
		else if (line.startsWith('-') && !line.startsWith('---')) deletions++;
	}
	return { insertions, deletions };
}

/**
 * Extract the ``diff`` field from a ToolResultBlock metadata bag, returning
 * ``undefined`` when missing or empty so callers can use it with ``??``.
 */
export function getResultDiff(result: { metadata?: Record<string, unknown> }): string | undefined {
	const diff = result.metadata?.diff;
	return typeof diff === 'string' && diff.length > 0 ? diff : undefined;
}

/**
 * Compact ``+N -M`` badge used in tool call headers for Edit / Write to show
 * how many lines were inserted and deleted.
 */
export function DiffStats({ insertions, deletions }: { insertions: number; deletions: number }) {
	return (
		<div className="flex items-center gap-0.5">
			<div className="flex items-center text-emerald-600 dark:text-emerald-400">
				<Plus className="size-2.5 stroke-2" />
				{formatNumber(insertions)}
			</div>
			<div className="flex items-center text-red-600 dark:text-red-400">
				<Minus className="size-2.5 stroke-2" />
				{formatNumber(deletions)}
			</div>
		</div>
	);
}
