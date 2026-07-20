import { defaultRenderBody } from './DefaultRenderer';
import { DiffPreview } from './DiffPreview';
import type { ToolRenderer } from './types';
import {
	countDiffStats,
	DiffStats,
	FramedFileBody,
	getResultDiff,
	toolArgClass,
	toolLabelClass,
	tryGetFileName,
	tryGetFilePath,
} from '@/components/chat/tool-renderers/_shared.tsx';

export const WriteRenderer: ToolRenderer = {
	getDisplayName: (call) => call.name,

	renderConfirmBody: (call) => (
		<div className="w-full max-w-full overflow-hidden text-ellipsis truncate">
			<div className="text-secondary-foreground">{tryGetFilePath(call.input)}</div>
		</div>
	),

	renderHeader: (pair) => {
		const fileName = tryGetFileName(pair.call.input);
		// Pre-execution we only know the new ``content`` (not the previous file
		// body), so any ``+N`` count would be misleading on overwrites. Show the
		// real ``+N -M`` only once the backend post-execution diff has arrived.
		const diff = pair.result ? getResultDiff(pair.result) : undefined;
		const stats = diff ? countDiffStats(diff) : null;
		return (
			<>
				<span className={toolLabelClass}>{pair.call.name}</span>
				{fileName && <span className={toolArgClass}>{fileName}</span>}
				{stats && <DiffStats insertions={stats.insertions} deletions={stats.deletions} />}
			</>
		);
	},

	renderBody: (pair, t) => {
		// Until the call has actually run there's no file change to frame — the
		// input is still streaming in as partial JSON.
		if (!pair.result || pair.result.state === 'running') return undefined;
		if (pair.result.state === 'success') {
			// The backend Write tool always attaches a unified diff (new-file
			// creation against /dev/null or an overwrite with absolute line
			// numbers). If it's missing we fall through rather than fabricate one.
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
