import type { ToolCallBlock, ToolResultBlock } from '@agentscope-ai/agentscope/message';
import { ChevronRight } from 'lucide-react';
import * as mime from 'mime-types';
import type { ReactNode } from 'react';

import { CornerLine, ToolStateIcon } from './_shared';
import type { TFunction, ToolCallWithResult } from './types';
import { Button } from '@/components/ui/button.tsx';
import {
	Collapsible,
	CollapsibleContent,
	CollapsibleTrigger,
} from '@/components/ui/collapsible.tsx';

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

/**
 * Default group layout: each call is an independent block with a state icon,
 * `displayName(args)` header line, and (optionally) a corner-line-prefixed
 * result block beneath it. Used by tools without a custom `renderGroup`,
 * e.g. arbitrary MCP tools.
 *
 * `getDisplayName` / `renderCallArgs` / `renderResult` are passed in from
 * `index.ts` so this function stays decoupled from the renderer registry.
 */
export function defaultRenderGroup(
	calls: ToolCallWithResult[],
	_t: TFunction,
	resolvers: {
		getDisplayName: (call: ToolCallBlock) => string;
		renderCallArgs: (call: ToolCallBlock) => ReactNode;
		renderResult: (call: ToolCallBlock, result: ToolResultBlock) => ReactNode;
	},
): ReactNode {
	return (
		<>
			{calls.map(({ call, result }) => {
				const displayName = resolvers.getDisplayName(call);
				const args = resolvers.renderCallArgs(call);
				const resultContent = result ? resolvers.renderResult(call, result) : null;
				return (
					<Collapsible key={call.id} className="flex flex-col w-full max-w-full text-sm">
						<CollapsibleTrigger asChild>
							<Button
								variant={'ghost'}
								className="group hover:bg-transparent data-[state=open]:bg-transparent justify-start px-0 gap-2 active:!translate-y-0"
							>
								<ToolStateIcon states={[result?.state]} />
								<strong className="shrink-0 text-primary">{displayName}</strong>
								{args && <span className="truncate min-w-0 text-left">{args}</span>}
								{resultContent && (
									<ChevronRight className="size-3 shrink-0 group-data-[state=open]:rotate-90" />
								)}
							</Button>
						</CollapsibleTrigger>
						{resultContent && (
							<CollapsibleContent className="flex flex-row gap-x-2 pl-6 max-w-full">
								<CornerLine />
								{resultContent}
							</CollapsibleContent>
						)}
					</Collapsible>
				);
			})}
		</>
	);
}
