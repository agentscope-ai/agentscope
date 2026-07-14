import { defaultRenderBody } from './DefaultRenderer';
import { DiffPreview } from './DiffPreview';
import type { ToolRenderer } from './types';
import {
	countDiffStats,
	DiffStats,
	FramedFileBody,
	getFilePath,
	getResultDiff,
	toolArgClass,
	toolLabelClass,
	tryGetFileName,
} from '@/components/chat/tool-renderers/_shared.tsx';

export const WriteRenderer: ToolRenderer = {
	getDisplayName: (call) => call.name,

	renderConfirmBody: (call) => (
		<div className="w-full max-w-full overflow-hidden text-ellipsis truncate">
			<div className="text-secondary-foreground">{getFilePath(call.input)}</div>
		</div>
	),

	renderHeader: (pair) => {
		const fileName = tryGetFileName(pair.call.input);
		if (!fileName) return <span className={toolLabelClass}>{pair.call.name}</span>;
		// Pre-execution we only know the new ``content`` (not the previous file
		// body), so any ``+N`` count would be misleading on overwrites. Show the
		// real ``+N -M`` only once the backend post-execution diff has arrived.
		const diff = pair.result ? getResultDiff(pair.result) : undefined;
		const stats = diff ? countDiffStats(diff) : null;
		return (
			<>
				<span className={toolLabelClass}>{pair.call.name}</span>
				<span className={toolArgClass}>{fileName}</span>
				{stats && <DiffStats insertions={stats.insertions} deletions={stats.deletions} />}
			</>
		);
	},

	renderBody: (pair, t) => {
		if (!pair.result) return null;
		if (pair.result.state === 'success') {
			// The backend Write tool always attaches a unified diff (new-file
			// creation against /dev/null or an overwrite with absolute line
			// numbers). If it's missing we fall through rather than fabricate one.
			const diff = getResultDiff(pair.result);
			if (diff) {
				return (
					<FramedFileBody filePath={getFilePath(pair.call.input)}>
						<DiffPreview unifiedDiff={diff} />
					</FramedFileBody>
				);
			}
		}
		return defaultRenderBody(pair, t);
	},
};
