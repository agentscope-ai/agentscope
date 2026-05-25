import type { ToolRenderer } from './types';

function parseInput(input: string): Record<string, unknown> {
	try {
		return JSON.parse(input);
	} catch {
		return {};
	}
}

export const ReadRenderer: ToolRenderer = {
	getDisplayName: () => 'Read',

	renderCallArgs: (call) => {
		const { file_path } = parseInput(call.input) as { file_path?: string };
		return file_path || call.input;
	},

	renderConfirmBody: (call) => {
		const { file_path } = parseInput(call.input) as { file_path?: string };
		return (
			<div className="w-full max-w-full overflow-hidden text-ellipsis truncate">
				<div className="text-secondary-foreground">{file_path}</div>
			</div>
		);
	},
};
