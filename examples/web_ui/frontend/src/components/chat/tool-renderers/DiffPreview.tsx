import { ChevronDown, MoreHorizontal, Minus, Plus } from 'lucide-react';
import type { ReactElement } from 'react';
import { useMemo, useState } from 'react';
import { Decoration, Diff, Hunk, parseDiff } from 'react-diff-view';
import type { ChangeData, DiffType, GutterOptions, HunkData } from 'react-diff-view';

import 'react-diff-view/style/index.css';

const MAX_VISIBLE_DIFF_LINES = 18;

interface DiffPreviewProps {
	/**
	 * Pre-computed unified-diff text (produced by the backend Edit / Write
	 * tools and delivered via ``ToolResultBlock.metadata.diff``). It carries
	 * absolute file line numbers and naturally handles multi-hunk diffs from
	 * ``replace_all``. The component renders nothing if this is empty.
	 */
	unifiedDiff: string;
}

function hunkLineCount(hunk: HunkData): number {
	return hunk.changes.length;
}

function getVisibleHunks(hunks: HunkData[], expanded: boolean): HunkData[] {
	if (expanded) return hunks;

	let visibleLineCount = 0;
	const visibleHunks: HunkData[] = [];

	for (const hunk of hunks) {
		const remaining = MAX_VISIBLE_DIFF_LINES - visibleLineCount;
		if (remaining <= 0) break;
		const hunkLines = hunkLineCount(hunk);
		if (hunkLines <= remaining) {
			visibleHunks.push(hunk);
			visibleLineCount += hunkLines;
			continue;
		}
		// Hunk doesn't fit in the remaining budget. If we haven't shown
		// anything yet (typical for new-file / full-rewrite diffs that
		// produce a single very large hunk), include a truncated slice so
		// the user still sees the start of the change. Otherwise stop so
		// we never overshoot ``MAX_VISIBLE_DIFF_LINES``.
		if (visibleHunks.length === 0) {
			visibleHunks.push(truncateHunkChanges(hunk, remaining));
		}
		break;
	}

	return visibleHunks;
}

/**
 * Build a new ``HunkData`` containing only the first ``maxLines`` changes of
 * ``hunk``. ``oldLines`` / ``newLines`` are recomputed from the slice so
 * react-diff-view's line-number accounting stays internally consistent.
 */
function truncateHunkChanges(hunk: HunkData, maxLines: number): HunkData {
	if (hunk.changes.length <= maxLines) return hunk;
	const changes = hunk.changes.slice(0, maxLines);
	let oldLines = 0;
	let newLines = 0;
	for (const change of changes) {
		if (change.type === 'normal') {
			oldLines += 1;
			newLines += 1;
		} else if (change.type === 'delete') {
			oldLines += 1;
		} else if (change.type === 'insert') {
			newLines += 1;
		}
	}
	return { ...hunk, changes, oldLines, newLines };
}

function countHiddenLines(allHunks: HunkData[], visibleHunks: HunkData[]): number {
	const total = allHunks.reduce((sum, hunk) => sum + hunkLineCount(hunk), 0);
	const visible = visibleHunks.reduce((sum, hunk) => sum + hunkLineCount(hunk), 0);
	return Math.max(0, total - visible);
}

function getLineClassName({ changes }: { changes: Array<{ type: string }> }): string {
	if (changes.some((change) => change.type === 'insert')) {
		return 'bg-emerald-500/10';
	}
	if (changes.some((change) => change.type === 'delete')) {
		return 'bg-red-500/10';
	}
	return 'bg-transparent';
}

// Pick the line number we want to display in the single visible gutter column.
// - normal rows show the new-side line number (mirrors GitHub's unified view)
// - insert / delete rows only have one `lineNumber` field, so use it directly
function getDisplayedLineNumber(change: ChangeData): number | undefined {
	if (change.type === 'normal') return change.newLineNumber;
	return change.lineNumber;
}

// Render only the "new" side of each row. The "old" side <col> is removed
// from the table layout via `visibility: collapse` (see arbitrary variants
// on the table className below), so the unified diff effectively renders
// as a single gutter column showing `<line-number> +/-`.
function renderGutter({ change, side }: GutterOptions) {
	if (side === 'old') return null;

	let marker = null;
	if (change.type === 'insert') {
		marker = <Plus className="size-2.5 text-emerald-600 dark:text-emerald-400" />;
	} else if (change.type === 'delete') {
		marker = <Minus className="size-2.5 text-red-600 dark:text-red-400" />;
	}

	return (
		<span className="inline-flex w-full items-center justify-between tabular-nums">
			<span>{getDisplayedLineNumber(change)}</span>
			{marker}
		</span>
	);
}

// Lines between two consecutive hunks that the unified diff omitted.
// ``hunk.oldStart`` is 1-based and ``oldLines`` is the count of old-side
// lines covered (including context). So the next hunk's ``oldStart`` minus
// the end of the previous hunk gives the gap.
function gapLinesBetween(prev: HunkData, next: HunkData): number {
	return next.oldStart - (prev.oldStart + prev.oldLines);
}

export function DiffPreview({ unifiedDiff }: DiffPreviewProps) {
	const [expanded, setExpanded] = useState(false);
	const diffFile = useMemo(
		() => parseDiff(unifiedDiff, { nearbySequences: 'zip' })[0],
		[unifiedDiff],
	);

	if (!diffFile || diffFile.hunks.length === 0) {
		return <div className="text-xs text-muted-foreground">No textual changes detected.</div>;
	}

	const visibleHunks = getVisibleHunks(diffFile.hunks, expanded);
	const hiddenLines = countHiddenLines(diffFile.hunks, visibleHunks);

	return (
		<Diff
			diffType={diffFile.type as DiffType}
			hunks={visibleHunks}
			viewType="unified"
			renderGutter={renderGutter}
			className="w-full border-collapse font-mono text-xs leading-5 [&>colgroup>col:first-child]:!w-0 [&>colgroup>col:first-child]:[visibility:collapse] [&_td.diff-gutter:first-of-type]:!p-0 [&_td.diff-gutter:first-of-type]:!border-r-0 [&_td.diff-gutter:first-of-type]:!w-0"
			hunkClassName="align-top"
			lineClassName="align-top"
			gutterClassName="select-none border-r border-border px-2 text-right text-muted-foreground"
			codeClassName="whitespace-pre-wrap break-all px-3"
			generateLineClassName={getLineClassName}
		>
			{(hunks) => {
				const children: ReactElement[] = [];

				hunks.forEach((hunk, idx) => {
					if (idx > 0) {
						// Insert an ellipsis decoration between hunks so it's
						// visually obvious there are skipped lines between
						// e.g. an edit at line 20 and another at line 70.
						const gap = gapLinesBetween(hunks[idx - 1], hunk);
						children.push(
							<Decoration key={`gap-${hunk.oldStart}-${hunk.newStart}`}>
								<div className="flex items-center gap-2 border-y border-dashed border-border bg-muted/30 px-3 py-1 text-[10px] text-muted-foreground select-none">
									<MoreHorizontal className="h-3 w-3" />
									<span className="tabular-nums">
										{gap > 0
											? `${gap} unchanged line${gap === 1 ? '' : 's'}`
											: 'unchanged lines'}
									</span>
								</div>
							</Decoration>,
						);
					}
					children.push(
						<Hunk key={`hunk-${hunk.oldStart}-${hunk.newStart}`} hunk={hunk} />,
					);
				});

				if (hiddenLines > 0) {
					children.push(
						<Decoration key="collapsed-diff-lines">
							<button
								type="button"
								className="flex w-full items-center justify-center gap-1 border-t border-border bg-muted/50 px-3 py-2 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
								onClick={() => setExpanded(true)}
							>
								<ChevronDown className="h-3.5 w-3.5" />
								{hiddenLines} more lines (click to expand)
							</button>
						</Decoration>,
					);
				}

				return children;
			}}
		</Diff>
	);
}
