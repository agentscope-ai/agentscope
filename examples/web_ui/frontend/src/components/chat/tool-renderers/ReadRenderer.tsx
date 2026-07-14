import {
	FramedFileBody,
	getResultText,
	toolArgClass,
	toolLabelClass,
	tryGetFileName,
	tryGetFilePath,
} from './_shared';
import { defaultRenderBody } from './DefaultRenderer';
import { DiffPreview } from './DiffPreview';
import type { ToolRenderer } from './types';

/**
 * Split a `cat -n` formatted Read result (`"    12\tcontent"`, produced by the
 * backend Read tool) into `{ num, text }` rows. Lines without a tab (e.g.
 * non-text/binary output) fall back to an empty line number.
 */
function parseNumberedLines(content: string): Array<{ num: string; text: string }> {
	return content.split('\n').map((line) => {
		const tab = line.indexOf('\t');
		if (tab === -1) return { num: '', text: line };
		return { num: line.slice(0, tab).trim(), text: line.slice(tab + 1) };
	});
}

/**
 * Render a Read result through the very same `DiffPreview` that Edit / Write
 * use, by synthesizing a unified diff whose every line is an unchanged context
 * line. Read isn't a diff, but presenting it as an all-context one gives it the
 * identical monospace layout and line-number gutter — just without +/- markers
 * or colouring — so all three file tools look consistent. The starting line
 * number is taken from the first `cat -n` gutter value so partial reads
 * (`offset`) still show absolute line numbers.
 */
function buildContextDiff(content: string): string {
	const rows = parseNumberedLines(content);
	const start = parseInt(rows[0]?.num ?? '', 10) || 1;
	const body = rows.map((row) => ` ${row.text}`).join('\n');
	return `--- a/file\n+++ b/file\n@@ -${start},${rows.length} +${start},${rows.length} @@\n${body}`;
}

export const ReadRenderer: ToolRenderer = {
	getDisplayName: (_call, t) => t('tool.read.name'),

	renderConfirmBody: (call) => (
		<div className="w-full max-w-full overflow-hidden text-ellipsis truncate">
			<div className="text-secondary-foreground">{tryGetFilePath(call.input)}</div>
		</div>
	),

	renderHeader: (pair) => {
		const fileName = tryGetFileName(pair.call.input);
		const readContent = getResultText(pair.result);
		const lines = readContent ? readContent.split('\n').length : 0;
		return (
			<>
				<span className={toolLabelClass}>Read</span>
				{fileName && <span className={toolArgClass}>{fileName}</span>}
				{pair.result?.state === 'success' && (
					<span className={toolLabelClass}>{lines} lines</span>
				)}
			</>
		);
	},

	renderBody: (pair, t) => {
		// Until the call has actually run there's no file content to frame — the
		// input is still streaming in as partial JSON.
		if (!pair.result || pair.result.state === 'running') return undefined;
		// Errors / interruptions aren't file content, so drop the path frame.
		if (pair.result.state !== 'success') return defaultRenderBody(pair, t);
		return (
			<FramedFileBody filePath={tryGetFilePath(pair.call.input)}>
				<DiffPreview unifiedDiff={buildContextDiff(getResultText(pair.result))} />
			</FramedFileBody>
		);
	},
};
