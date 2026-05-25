import type { ToolRenderer } from './types';

function parseInput(input: string): Record<string, unknown> {
	try {
		return JSON.parse(input);
	} catch {
		return {};
	}
}

export const SearchRenderer: ToolRenderer = {
	getDisplayName: (call) => call.name,

	renderCallArgs: (call) => {
		const { pattern } = parseInput(call.input) as { pattern?: string };
		return pattern || call.input;
	},
};
