import type { ToolResultBlock } from '@agentscope-ai/agentscope/message';
import { Ban, Check, ChevronRight, LoaderCircle, Minus, Plus, X } from 'lucide-react';
import type { ReactNode } from 'react';

import type { ToolCallWithResult } from './types';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { Separator } from '@/components/ui/separator';
import { cn } from '@/lib/utils';
import { formatNumber } from '@/utils/common.ts';

export function ToolStateIcon({ state }: { state: ToolResultBlock['state'] | undefined }) {
	if (state === 'success') {
		return <Check className="size-3 text-emerald-600 dark:text-emerald-400 shrink-0" />;
	}
	if (state === 'error') {
		return <X className="size-3 text-red-600 dark:text-red-400  shrink-0" />;
	}
	if (state === 'interrupted' || state === 'denied') {
		return <Ban className="size-3 !h-3 min-h-3 shrink-0" />;
	}

	// running
	return <LoaderCircle className="size-3 shrink-0 animate-spin" />;
}

/**
 * Flatten a tool result's ``output`` (string or block array) into plain text,
 * keeping only the text blocks. Returns ``''`` when the result is missing.
 */
export function getResultText(result?: ToolResultBlock): string {
	if (!result) return '';
	if (typeof result.output === 'string') return result.output;
	if (Array.isArray(result.output)) {
		return result.output.map((b) => (b.type === 'text' ? b.text : '')).join('\n');
	}
	return '';
}

/**
 * Shared class for the *leading label* of a tool-call trigger line — the verb
 * or tool name such as "Read", "Bash", "Grep pattern", or the generic
 * "Call tool". Deliberately not bold (no `<strong>`): every tool's label looks
 * the same and only brightens to the foreground colour on row hover (the row
 * is a `group`).
 */
export const toolLabelClass = 'shrink-0 transition-colors group-hover:text-foreground';

/**
 * Shared class for the *primary argument* of a trigger line — the file name
 * (Read/Edit/Write), search pattern (Grep/Glob), task subject (TaskCreate) or,
 * for tools without a dedicated renderer, the tool name itself. Unifies weight
 * and truncation so the second slot is visually identical across every tool.
 */
export const toolArgClass =
	'font-[450] min-w-0 truncate transition-colors group-hover:text-foreground';

/**
 * One collapsible tool-call row — the single shared shell every tool renders
 * through. The trigger line is ``{header}  <state-icon>  <chevron>``; the
 * chevron only appears on hover and stays visible (rotated down) while open.
 * When ``body`` is provided the row expands to reveal it; without a body the
 * row is a plain, non-expandable line (no chevron, no pointer cursor).
 *
 * ``header`` should be a fragment of inline flex children (the parent supplies
 * ``gap-x-2``); tools never touch the Collapsible / state icon themselves.
 */
export function ToolCallRow({
	pair,
	header,
	body,
}: {
	pair: ToolCallWithResult;
	header: ReactNode;
	body?: ReactNode;
}) {
	const expandable = body != null && body !== false;
	// Shimmer the header text. The shadcn ``shimmer`` util is a text-clip effect,
	// so it has to sit on the elements that *directly* hold the text — the
	// header's own leaf spans — not on a wrapper (a wrapper only makes descendant
	// text transparent). We target the direct span children of this flex row
	// instead of wrapping ``header``, which also keeps their ``gap-x-2`` spacing.
	const row = (
		<div
			className={cn(
				'group flex flex-row gap-x-2 items-center w-full',
				expandable && 'cursor-pointer',
				!pair.result || pair.result.state === 'running' ? 'shimmer' : '',
			)}
		>
			{header}
			<ToolStateIcon state={pair.result?.state} />
			{expandable && (
				<ChevronRight
					className={
						'size-3 shrink-0 transition-transform hidden group-hover:flex group-data-[state=open]:flex group-data-[state=open]:rotate-90'
					}
				/>
			)}
		</div>
	);

	if (!expandable) return row;

	return (
		<Collapsible>
			<CollapsibleTrigger asChild>{row}</CollapsibleTrigger>
			<CollapsibleContent>{body}</CollapsibleContent>
		</Collapsible>
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
 * this in ``renderHeader`` so partial JSON streamed during tool-call
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
 * Framed body box shared by file-oriented tools (Read / Edit / Write): a
 * bordered card with the file path as a header, a separator, then the tool's
 * own content (numbered source lines for Read, a diff for Edit / Write).
 */
export function FramedFileBody({ filePath, children }: { filePath: string; children: ReactNode }) {
	return (
		<div className="flex flex-col border rounded-sm bg-background">
			<div className="px-2 py-1 whitespace-nowrap overflow-x-auto">{filePath}</div>
			<Separator />
			{children}
		</div>
	);
}

/**
 * Compact ``+N -M`` badge used in tool call headers for Edit / Write to show
 * how many lines were inserted and deleted.
 */
export function DiffStats({ insertions, deletions }: { insertions: number; deletions: number }) {
	return (
		<div className="flex items-center gap-0.5">
			{insertions && (
				<div className="flex items-center text-emerald-600 dark:text-emerald-400">
					<Plus className="size-2.5 stroke-2" />
					{formatNumber(insertions)}
				</div>
			)}
			{deletions && (
				<div className="flex items-center text-red-600 dark:text-red-400">
					<Minus className="size-2.5 stroke-2" />
					{formatNumber(deletions)}
				</div>
			)}
		</div>
	);
}
