import { ToolCallGroupList } from './_shared';
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

export const ReadRenderer: ToolRenderer = {
	getDisplayName: (_call, t) => t('tool.read.name'),

	renderCallArgs: (call) => getFilePath(call.input),

	renderConfirmBody: (call) => {
		return (
			<div className="w-full max-w-full overflow-hidden text-ellipsis truncate">
				<div className="text-secondary-foreground">{getFilePath(call.input)}</div>
			</div>
		);
	},

	renderGroup: (calls, t) => (
		<ToolCallGroupList
			calls={calls}
			label={
				<span className="text-sm">
					<strong className="truncate text-primary">{t('tool.read.name')} </strong>
					{t('tool.read.fileCount', { count: calls.length })}
				</span>
			}
			renderItem={(item) => getFilePath(item.call.input)}
		/>
	),
};
