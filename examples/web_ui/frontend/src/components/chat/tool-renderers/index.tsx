import type { DataBlock, ToolCallBlock } from '@agentscope-ai/agentscope/message';
import type { ReactNode } from 'react';

import { ToolCallRow } from './_shared';
import {
	dataBlockName,
	dataBlockDownloadUrl,
	fileFromDataBlock,
	isOfficeDocument,
	OfficeFilePreview,
} from '../OfficeFilePreview';
import { BashRenderer } from './BashRenderer';
import {
	defaultGetDisplayName,
	defaultRenderBody,
	defaultRenderConfirmBody,
	defaultRenderHeader,
} from './DefaultRenderer';
import { EditRenderer } from './EditRenderer';
import { GlobRenderer } from './GlobRenderer';
import { GrepRenderer } from './GrepRenderer';
import { ReadRenderer } from './ReadRenderer';
import { TaskCreateRenderer } from './TaskCreateRenderer';
import type { TFunction, ToolCallWithResult, ToolRenderer, ToolRenderContext } from './types';
import { WriteRenderer } from './WriteRenderer';

const renderers: Record<string, ToolRenderer> = {
	Bash: BashRenderer,
	Read: ReadRenderer,
	Write: WriteRenderer,
	Edit: EditRenderer,
	Glob: GlobRenderer,
	Grep: GrepRenderer,
	TaskCreate: TaskCreateRenderer,
};

function getRenderer(toolName: string): ToolRenderer {
	return renderers[toolName] ?? {};
}

export function getDisplayName(call: ToolCallBlock, t: TFunction): string {
	const r = getRenderer(call.name);
	return r.getDisplayName?.(call, t) ?? defaultGetDisplayName(call);
}

export function renderConfirmBody(call: ToolCallBlock, t: TFunction): ReactNode {
	const r = getRenderer(call.name);
	return r.renderConfirmBody?.(call, t) ?? defaultRenderConfirmBody(call);
}

/**
 * Render a single tool call as one collapsible row. The tool's renderer only
 * supplies the trigger-line `header` and the expandable `body`; the shared
 * `ToolCallRow` owns the collapsible shell, state icon and chevron. Falls back
 * to the `Default*` implementations for tools without a dedicated renderer.
 */
export function renderToolCall(
	pair: ToolCallWithResult,
	t: TFunction,
	context?: ToolRenderContext,
): ReactNode {
	const r = getRenderer(pair.call.name);
	const header = r.renderHeader?.(pair, t) ?? defaultRenderHeader(pair, t);
	const body = r.renderBody?.(pair, t, context) ?? defaultRenderBody(pair, t, context);
	const officeBlocks: DataBlock[] =
		context?.agentId && context.sessionId && Array.isArray(pair.result?.output)
			? pair.result.output.filter(
					(block): block is DataBlock =>
						block.type === 'data' &&
						isOfficeDocument(dataBlockName(block), block.source.media_type),
				)
			: [];
	const content = officeBlocks.length ? (
		<div className="flex min-w-0 flex-col gap-2">
			{body}
			{officeBlocks.map((block) => {
				const name = dataBlockName(block);
				return (
					<OfficeFilePreview
						key={block.id}
						agentId={context!.agentId!}
						sessionId={context!.sessionId!}
						name={name}
						mediaType={block.source.media_type}
						loadOriginal={() =>
							fileFromDataBlock(block, name, context!.agentId!, context!.sessionId!)
						}
						getDownloadUrl={() =>
							dataBlockDownloadUrl(block, context!.agentId!, context!.sessionId!)
						}
						downloadFromSource
					/>
				);
			})}
		</div>
	) : (
		body
	);
	return <ToolCallRow key={pair.call.id} pair={pair} header={header} body={content} />;
}
