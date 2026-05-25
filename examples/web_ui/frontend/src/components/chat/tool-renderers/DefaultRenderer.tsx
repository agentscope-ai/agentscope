import type { ToolCallBlock, ToolResultBlock } from '@agentscope-ai/agentscope/message';
import * as mime from 'mime-types';
import type { ReactNode } from 'react';

import type { TFunction } from './types';

function processToolInput(input: string): string {
	try {
		const obj = JSON.parse(input);
		const entries = Object.entries(obj).map(([k, v]) => `${k}: "${v}"`);
		return entries.join('\n');
	} catch {
		return input;
	}
}

export function defaultGetDisplayName(call: ToolCallBlock): string {
	return call.name;
}

export function defaultRenderCallArgs(call: ToolCallBlock): ReactNode {
	if (call.input.length <= 2) return null;
	return processToolInput(call.input);
}

export function defaultRenderResult(
	call: ToolCallBlock,
	result: ToolResultBlock,
	t: TFunction,
	maxLines = 7,
): ReactNode {
	if (call.state === 'asking' || !result || result.state === 'running') {
		return <span>{t('common.running')} ...</span>;
	}
	if (result.state === 'interrupted') {
		return <span>{t('common.interrupted')}</span>;
	}

	let resultStr: string;
	if (typeof result.output === 'string') {
		resultStr = result.output;
	} else {
		const parts = result.output.map((b) => {
			if (b.type === 'text') return b.text;
			const mainType = b.source.media_type.split('/')[0].toUpperCase();
			const ext = (mime.extension(b.source.media_type) || 'bin').toLowerCase();
			return `[${mainType}.${ext}]`;
		});
		resultStr = parts.join('\n');
	}

	let lines = resultStr.split('\n');
	if (lines.length > maxLines) {
		const total = lines.length;
		lines = lines.slice(0, maxLines);
		lines.push(t('tool.moreLines', { count: total - maxLines }));
	}

	return (
		<div className="flex flex-col flex-1 min-w-0">
			{lines.map((line, i) => (
				<div key={i} className="truncate">
					{line}
				</div>
			))}
		</div>
	);
}

export function defaultRenderConfirmBody(call: ToolCallBlock): ReactNode {
	return (
		<div className="w-full max-w-full overflow-hidden text-ellipsis truncate">
			<div className="text-secondary-foreground">{call.input}</div>
		</div>
	);
}
