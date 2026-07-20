import unidiff from 'unidiff';

import { defaultRenderBody } from './DefaultRenderer';
import { DiffPreview } from './DiffPreview';
import type { ToolCallWithResult, ToolRenderer } from './types';
import {
	countDiffStats,
	DiffStats,
	FramedFileBody,
	getResultDiff,
	parseInput,
	toolArgClass,
	toolLabelClass,
	tryGetFileName,
	tryGetFilePath,
} from '@/components/chat/tool-renderers/_shared.tsx';

/**
 * Count the real inserted / deleted lines between ``oldText`` and ``newText``
 * by computing a unified diff and tallying the leading ``+`` / ``-`` markers.
 * This is the per-occurrence diff size — for ``replace_all`` the backend
 * reports the totalled counts via ``result.metadata`` (see ``countDiffStats``).
 */
function countLineChanges(
	oldText: string,
	newText: string,
): { insertions: number; deletions: number } {
	const diffText = unidiff.diffAsText(oldText, newText, { context: 0 });
	return countDiffStats(diffText);
}

/**
 * Insert/delete counts for the header badge. Prefer the backend post-execution
 * diff (correct for ``replace_all`` and absolute line numbers); before it
 * arrives, fall back to a per-occurrence client-side estimate of
 * ``old_string`` / ``new_string``.
 */
function headerStats(pair: ToolCallWithResult): { insertions: number; deletions: number } {
	const resultDiff = pair.result ? getResultDiff(pair.result) : undefined;
	if (resultDiff) return countDiffStats(resultDiff);
	const input = parseInput(pair.call.input);
	const oldString = typeof input.old_string === 'string' ? input.old_string : '';
	const newString = typeof input.new_string === 'string' ? input.new_string : '';
	return countLineChanges(oldString, newString);
}

export const EditRenderer: ToolRenderer = {
	getDisplayName: (call) => call.name,

	renderConfirmBody: (call) => (
		<div className="w-full max-w-full overflow-hidden text-ellipsis truncate">
			<div className="text-secondary-foreground">{tryGetFilePath(call.input)}</div>
		</div>
	),

	renderHeader: (pair) => {
		// While the tool-call JSON is still streaming, ``call.input`` is a partial
		// dict and the file name / diff can't be trusted yet — show just the label.
		const fileName = tryGetFileName(pair.call.input);
		if (!fileName) return <span className={toolLabelClass}>{pair.call.name}</span>;
		const { insertions, deletions } = headerStats(pair);
		return (
			<>
				<span className={toolLabelClass}>{pair.call.name}</span>
				<span className={toolArgClass}>{fileName}</span>
				<DiffStats insertions={insertions} deletions={deletions} />
			</>
		);
	},

	renderBody: (pair, t) => {
		// Until the call has actually run there's no file change to frame — the
		// input is still streaming in as partial JSON.
		if (!pair.result || pair.result.state === 'running') return undefined;
		if (pair.result.state === 'success') {
			// The backend Edit tool always attaches a unified diff (absolute line
			// numbers, one hunk per replaced occurrence). A client-side diff of
			// old_string/new_string would be misleading, so if it's missing we
			// fall through to the default result body rather than fabricate one.
			const diff = getResultDiff(pair.result);
			if (diff) {
				return (
					<FramedFileBody filePath={tryGetFilePath(pair.call.input)}>
						<DiffPreview unifiedDiff={diff} />
					</FramedFileBody>
				);
			}
		}
		// Errors / interruptions aren't file content, so drop the path frame.
		return defaultRenderBody(pair, t);
	},
};
