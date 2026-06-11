import { ChevronDown } from 'lucide-react';
import { useMemo, useState } from 'react';
import { Decoration, Diff, Hunk, parseDiff } from 'react-diff-view';
import type { DiffType, HunkData } from 'react-diff-view';
import unidiff from 'unidiff';

const MAX_VISIBLE_DIFF_LINES = 18;

interface DiffPreviewProps {
	filePath: string;
	oldText: string;
	newText: string;
	oldFileName?: string;
	newFileName?: string;
}

function hunkLineCount(hunk: HunkData): number {
	return hunk.changes.length;
}

function getVisibleHunks(hunks: HunkData[], expanded: boolean): HunkData[] {
	if (expanded) return hunks;

	let visibleLineCount = 0;
	const visibleHunks: HunkData[] = [];

	for (const hunk of hunks) {
		const nextLineCount = visibleLineCount + hunkLineCount(hunk);
		if (visibleHunks.length > 0 && nextLineCount > MAX_VISIBLE_DIFF_LINES) {
			break;
		}
		visibleHunks.push(hunk);
		visibleLineCount = nextLineCount;
	}

	return visibleHunks;
}

function countHiddenLines(hunks: HunkData[], visibleHunks: HunkData[]): number {
	const visible = new Set(visibleHunks);
	return hunks.reduce((count, hunk) => {
		return visible.has(hunk) ? count : count + hunkLineCount(hunk);
	}, 0);
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

export function DiffPreview({
	filePath,
	oldText,
	newText,
	oldFileName = `a/${filePath}`,
	newFileName = `b/${filePath}`,
}: DiffPreviewProps) {
	const [expanded, setExpanded] = useState(false);
	const diffFile = useMemo(() => {
		const diffText = unidiff.diffAsText(oldText, newText, {
			aname: oldFileName,
			bname: newFileName,
			context: 3,
		});
		return parseDiff(diffText, { nearbySequences: 'zip' })[0];
	}, [newFileName, newText, oldFileName, oldText]);

	if (!diffFile || diffFile.hunks.length === 0) {
		return <div className="text-xs text-muted-foreground">No textual changes detected.</div>;
	}

	const visibleHunks = getVisibleHunks(diffFile.hunks, expanded);
	const hiddenLines = countHiddenLines(diffFile.hunks, visibleHunks);

	return (
		<div className="flex min-w-0 flex-1 flex-col overflow-hidden rounded-md border border-border bg-background">
			<div className="border-b border-border px-3 py-2 font-mono text-xs text-muted-foreground">
				{filePath}
			</div>
			<div className="max-w-full overflow-x-auto">
				<Diff
					diffType={diffFile.type as DiffType}
					hunks={visibleHunks}
					viewType="unified"
					gutterType="default"
					className="w-full border-collapse font-mono text-xs leading-5"
					hunkClassName="align-top"
					lineClassName="align-top"
					gutterClassName="select-none border-r border-border px-2 text-right text-muted-foreground"
					codeClassName="whitespace-pre px-3"
					generateLineClassName={getLineClassName}
				>
					{(hunks) => {
						const children = hunks.map((hunk) => (
							<Hunk key={hunk.content} hunk={hunk} />
						));

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
			</div>
		</div>
	);
}
