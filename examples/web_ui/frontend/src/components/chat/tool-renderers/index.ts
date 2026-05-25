import type { ToolCallBlock, ToolResultBlock } from '@agentscope-ai/agentscope/message';
import type { ReactNode } from 'react';

import { BashRenderer } from './BashRenderer';
import {
	defaultGetDisplayName,
	defaultRenderCallArgs,
	defaultRenderResult,
	defaultRenderConfirmBody,
} from './DefaultRenderer';
import { ReadRenderer } from './ReadRenderer';
import { SearchRenderer } from './SearchRenderer';
import type { TFunction, ToolRenderer } from './types';
import { WriteRenderer } from './WriteRenderer';

const renderers: Record<string, ToolRenderer> = {
	Bash: BashRenderer,
	Read: ReadRenderer,
	Write: WriteRenderer,
	Edit: WriteRenderer,
	Glob: SearchRenderer,
	Grep: SearchRenderer,
};

function getRenderer(toolName: string): ToolRenderer {
	return renderers[toolName] ?? {};
}

export function getDisplayName(call: ToolCallBlock, t: TFunction): string {
	const r = getRenderer(call.name);
	return r.getDisplayName?.(call, t) ?? defaultGetDisplayName(call);
}

export function renderCallArgs(call: ToolCallBlock, t: TFunction): ReactNode {
	const r = getRenderer(call.name);
	return r.renderCallArgs?.(call, t) ?? defaultRenderCallArgs(call);
}

export function renderResult(
	call: ToolCallBlock,
	result: ToolResultBlock,
	t: TFunction,
): ReactNode {
	const r = getRenderer(call.name);
	return r.renderResult?.(call, result, t) ?? defaultRenderResult(call, result, t);
}

export function renderConfirmBody(call: ToolCallBlock, t: TFunction): ReactNode {
	const r = getRenderer(call.name);
	return r.renderConfirmBody?.(call, t) ?? defaultRenderConfirmBody(call);
}
