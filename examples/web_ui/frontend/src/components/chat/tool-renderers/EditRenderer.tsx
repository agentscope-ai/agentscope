import unidiff from 'unidiff';

import {
	defaultGetDisplayName,
	defaultRenderCallArgs,
	defaultRenderGroup,
	defaultRenderResult,
} from './DefaultRenderer';
import { DiffPreview } from './DiffPreview';
import type { ToolRenderer } from './types';
import {
	countDiffStats,
	DiffStats,
	getFilePath,
	getResultDiff,
	parseInput,
	tryGetFileName,
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

function renderEditDiff(result: { metadata?: Record<string, unknown> }) {
	// The post-execution diff is always produced by the backend Edit tool
	// (with absolute line numbers and one hunk per replaced occurrence).
	// If it's missing we deliberately render nothing — falling back to a
	// client-side diff of ``old_string`` / ``new_string`` would silently
	// produce misleading line numbers and miss the other replace_all
	// occurrences.
	const unifiedDiff = getResultDiff(result);
	if (!unifiedDiff) return null;
	return <DiffPreview unifiedDiff={unifiedDiff} />;
}

export const EditRenderer: ToolRenderer = {
	getDisplayName: (call) => call.name,

	renderCallArgs: (call) => {
		// While the tool-call JSON is still streaming, ``call.input`` is a
		// partial dict and parsing yields the wrong file name / empty
		// strings. Return an empty fragment (not ``null``) so the index.ts
		// ``?? defaultRenderCallArgs`` fallback doesn't dump the raw JSON.
		const fileName = tryGetFileName(call.input);
		if (!fileName) return <></>;
		const input = parseInput(call.input);
		const oldString = typeof input.old_string === 'string' ? input.old_string : '';
		const newString = typeof input.new_string === 'string' ? input.new_string : '';
		// Use the real per-occurrence insert/delete counts rather than the
		// raw line counts of old_string / new_string (which over-count when
		// most lines are unchanged context).
		const { insertions, deletions } = countLineChanges(oldString, newString);

		return (
			<div className="flex items-center gap-2 font-normal">
				{fileName}
				<DiffStats insertions={insertions} deletions={deletions} />
			</div>
		);
	},

	renderConfirmBody: (call) => (
		<div className="w-full max-w-full overflow-hidden text-ellipsis truncate">
			<div className="text-secondary-foreground">{getFilePath(call.input)}</div>
		</div>
	),

	renderGroup: (calls, t) =>
		defaultRenderGroup(calls, t, {
			getDisplayName: (call) =>
				EditRenderer.getDisplayName?.(call, t) ?? defaultGetDisplayName(call),
			renderCallArgs: (call) => {
				// When the backend has provided the post-execution unified diff
				// (handles replace_all correctly), prefer its insert/delete
				// counts over the per-occurrence client-side estimate.
				const enriched = calls.find((c) => c.call.id === call.id);
				const resultDiff = enriched?.result
					? getResultDiff(enriched.result as { metadata?: Record<string, unknown> })
					: undefined;
				if (resultDiff) {
					const fileName = tryGetFileName(call.input);
					if (!fileName) return null;
					const { insertions, deletions } = countDiffStats(resultDiff);
					return (
						<div className="flex items-center gap-2 font-normal">
							{fileName}
							<DiffStats insertions={insertions} deletions={deletions} />
						</div>
					);
				}
				return EditRenderer.renderCallArgs?.(call, t) ?? defaultRenderCallArgs(call);
			},
			renderResult: (call, result) =>
				(result.state === 'success' ? renderEditDiff(result) : null) ??
				EditRenderer.renderResult?.(call, result, t) ??
				defaultRenderResult(call, result, t),
		}),
};
