import {
	defaultGetDisplayName,
	defaultRenderCallArgs,
	defaultRenderGroup,
	defaultRenderResult,
} from './DefaultRenderer';
import { DiffPreview } from './DiffPreview';
import type { ToolRenderer } from './types';

function parseInput(input: string): Record<string, unknown> {
	try {
		return JSON.parse(input);
	} catch {
		return {};
	}
}

function getFilePath(input: string): string {
	const { file_path } = parseInput(input) as { file_path?: string };
	return file_path || input;
}

function renderWriteDiff(input: string) {
	const { file_path, content } = parseInput(input) as {
		file_path?: string;
		content?: string;
	};

	if (typeof file_path !== 'string' || typeof content !== 'string') {
		return null;
	}

	return (
		<DiffPreview
			filePath={file_path}
			oldText=""
			newText={content}
			oldFileName="/dev/null"
			newFileName={`b/${file_path}`}
		/>
	);
}

export const WriteRenderer: ToolRenderer = {
	getDisplayName: (call) => call.name,

	renderCallArgs: (call) => getFilePath(call.input),

	renderConfirmBody: (call) => (
		<div className="w-full max-w-full overflow-hidden text-ellipsis truncate">
			<div className="text-secondary-foreground">{getFilePath(call.input)}</div>
		</div>
	),

	renderGroup: (calls, t) =>
		defaultRenderGroup(calls, t, {
			getDisplayName: (call) =>
				WriteRenderer.getDisplayName?.(call, t) ?? defaultGetDisplayName(call),
			renderCallArgs: (call) =>
				WriteRenderer.renderCallArgs?.(call, t) ?? defaultRenderCallArgs(call),
			renderResult: (call, result) =>
				(result.state === 'success' ? renderWriteDiff(call.input) : null) ??
				WriteRenderer.renderResult?.(call, result, t) ??
				defaultRenderResult(call, result, t),
		}),
};
