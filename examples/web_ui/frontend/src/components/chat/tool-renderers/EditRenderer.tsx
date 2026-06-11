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

function renderEditDiff(input: string) {
	const { file_path, old_string, new_string } = parseInput(input) as {
		file_path?: string;
		old_string?: string;
		new_string?: string;
	};

	if (
		typeof file_path !== 'string' ||
		typeof old_string !== 'string' ||
		typeof new_string !== 'string'
	) {
		return null;
	}

	return <DiffPreview filePath={file_path} oldText={old_string} newText={new_string} />;
}

export const EditRenderer: ToolRenderer = {
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
				EditRenderer.getDisplayName?.(call, t) ?? defaultGetDisplayName(call),
			renderCallArgs: (call) =>
				EditRenderer.renderCallArgs?.(call, t) ?? defaultRenderCallArgs(call),
			renderResult: (call, result) =>
				(result.state === 'success' ? renderEditDiff(call.input) : null) ??
				EditRenderer.renderResult?.(call, result, t) ??
				defaultRenderResult(call, result, t),
		}),
};
