import type { ToolCallBlock } from '@agentscope-ai/agentscope/message';
import * as mime from 'mime-types';
import type { ReactNode } from 'react';

import { getResultText, toolArgClass, toolLabelClass } from './_shared';
import type { TFunction, ToolCallWithResult } from './types';

export function defaultGetDisplayName(call: ToolCallBlock): string {
	return call.name;
}

export function defaultRenderConfirmBody(call: ToolCallBlock): ReactNode {
	return (
		<div className="w-full max-w-full overflow-hidden text-ellipsis truncate">
			<div className="text-secondary-foreground">{call.input}</div>
		</div>
	);
}

/**
 * Default trigger line for tools without a custom `renderHeader` (e.g. MCP
 * tools): the generic "Call tool" label followed by the tool name rendered in
 * the shared argument style, so a nameless tool still reads as an action.
 */
export function defaultRenderHeader(pair: ToolCallWithResult, t: TFunction): ReactNode {
	return (
		<>
			<span className={toolLabelClass}>{t('tool.callGeneric')}</span>
			<span className={toolArgClass}>{defaultGetDisplayName(pair.call)}</span>
		</>
	);
}

/**
 * Default expandable body: the result output as text. The container caps its
 * height and scrolls, so no line counting / truncation is needed. Returns
 * `null` before the call has any result so the row stays non-expandable.
 */
export function defaultRenderBody(pair: ToolCallWithResult, t: TFunction): ReactNode {
	const { call, result } = pair;
	if (!result) return null;
	if (call.state === 'asking' || result.state === 'running') {
		return (
			<div className="flex flex-col border rounded-sm bg-background">
				<div className="px-2 py-1 whitespace-nowrap overflow-x-auto">
					{t('common.running')}
				</div>
			</div>
		);
	}
	if (result.state === 'interrupted') {
		const result = getResultText(pair.result);
		return (
			<div className="flex flex-col border rounded-sm bg-background">
				<div className="px-2 py-1 whitespace-nowrap overflow-x-auto">{result}</div>
			</div>
		);
	}

	// TODO: render multimodal outputs
	let text: string;
	if (typeof result.output === 'string') {
		text = result.output;
	} else {
		text = result.output
			.map((b) => {
				if (b.type === 'text') return b.text;
				const mainType = b.source.media_type.split('/')[0].toUpperCase();
				const ext = (mime.extension(b.source.media_type) || 'bin').toLowerCase();
				return `[${mainType}.${ext}]`;
			})
			.join('\n');
	}

	return (
		<pre className="border rounded-sm bg-background p-2 text-xs overflow-auto max-h-[200px] whitespace-pre-wrap">
			{text}
		</pre>
	);
}
