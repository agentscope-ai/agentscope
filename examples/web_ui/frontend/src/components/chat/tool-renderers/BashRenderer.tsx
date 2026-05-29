import {
	defaultGetDisplayName,
	defaultRenderCallArgs,
	defaultRenderGroup,
	defaultRenderResult,
} from './DefaultRenderer';
import type { ToolRenderer } from './types';

function parseInput(input: string): Record<string, unknown> {
	try {
		return JSON.parse(input);
	} catch {
		return {};
	}
}

export const BashRenderer: ToolRenderer = {
	getDisplayName: () => 'Bash',

	renderCallArgs: (call) => {
		const { command } = parseInput(call.input) as { command?: string };
		return command || call.input;
	},

	renderResult: (call, result, t) => {
		if (call.state === 'asking' || !result || result.state === 'running') {
			return t('common.running');
		}
		if (result.state === 'interrupted') {
			return t('common.interrupted');
		}
		return undefined;
	},

	renderConfirmBody: (call) => {
		const { command, description } = parseInput(call.input) as {
			command?: string;
			description?: string;
		};
		return (
			<div className="w-full max-w-full overflow-hidden text-ellipsis truncate">
				<div className="text-secondary-foreground font-mono">{command}</div>
				{description && <div className="text-muted-foreground">{description}</div>}
			</div>
		);
	},

	renderGroup: (calls, t) =>
		defaultRenderGroup(calls, t, {
			getDisplayName: (call) =>
				BashRenderer.getDisplayName?.(call, t) ?? defaultGetDisplayName(call),
			renderCallArgs: (call) =>
				BashRenderer.renderCallArgs?.(call, t) ?? defaultRenderCallArgs(call),
			renderResult: (call, result) =>
				BashRenderer.renderResult?.(call, result, t) ??
				defaultRenderResult(call, result, t),
		}),
};
