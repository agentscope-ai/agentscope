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
	tryGetFileName,
} from '@/components/chat/tool-renderers/_shared.tsx';

export const WriteRenderer: ToolRenderer = {
	getDisplayName: (call) => call.name,

	renderCallArgs: (call) => {
		// While the tool-call JSON is still streaming, ``call.input`` is a
		// partial dict (e.g. ``{"file_path": "foo", "content": "...``) and
		// parsing yields the wrong file name or none at all. Render an empty
		// fragment (not ``null``) so the index.ts ``?? defaultRenderCallArgs``
		// fallback doesn't kick in and dump the raw JSON string.
		const fileName = tryGetFileName(call.input);
		if (!fileName) return <></>;
		// Pre-execution we only know the new ``content`` (not the previous
		// file body), so any ``+N`` count would be misleading on overwrites.
		// The post-execution renderGroup override below adds ``+N -M`` once
		// ``metadata.diff`` arrives.
		return <div className="flex items-center gap-2 font-normal">{fileName}</div>;
	},

	renderConfirmBody: (call) => (
		<div className="w-full max-w-full overflow-hidden text-ellipsis truncate">
			<div className="text-secondary-foreground">{getFilePath(call.input)}</div>
		</div>
	),

	renderResult: (call, result, t) => {
		if (result.state === 'success') {
			// The backend Write tool always attaches a unified diff in
			// ``metadata.diff`` (correctly representing both new-file
			// creation against /dev/null and overwrites of existing files
			// with absolute line numbers). If it's missing we fall through
			// rather than render a misleading client-side diff.
			const unifiedDiff = getResultDiff(result as { metadata?: Record<string, unknown> });
			if (unifiedDiff) {
				return <DiffPreview unifiedDiff={unifiedDiff} />;
			}
			return undefined;
		}
		if (call.state === 'asking' || !result || result.state === 'running') {
			return t('common.running');
		}
		if (result.state === 'interrupted') {
			return t('common.interrupted');
		}
		return undefined;
	},

	renderGroup: (calls, t) =>
		defaultRenderGroup(calls, t, {
			getDisplayName: (call) =>
				WriteRenderer.getDisplayName?.(call, t) ?? defaultGetDisplayName(call),
			renderCallArgs: (call) => {
				// Once the backend has produced the post-execution unified
				// diff, compute and show the real ``+N -M`` stats — this
				// correctly accounts for overwrites of existing files
				// (which the pre-execution renderCallArgs cannot know).
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
				return WriteRenderer.renderCallArgs?.(call, t) ?? defaultRenderCallArgs(call);
			},
			renderResult: (call, result) =>
				WriteRenderer.renderResult?.(call, result, t) ??
				defaultRenderResult(call, result, t),
		}),
};
