import type { ToolCallBlock, ToolResultBlock } from '@agentscope-ai/agentscope/message';
import type { ReactNode } from 'react';

import { BashRenderer } from './BashRenderer';
import {
	defaultGetDisplayName,
	defaultRenderCallArgs,
	defaultRenderConfirmBody,
	defaultRenderGroup,
	defaultRenderResult,
} from './DefaultRenderer';
import { EditRenderer } from './EditRenderer';
import { GlobRenderer } from './GlobRenderer';
import { GrepRenderer } from './GrepRenderer';
import { ReadRenderer } from './ReadRenderer';
import { TaskCreateRenderer } from './TaskCreateRenderer';
import type { TFunction, ToolCallWithResult, ToolRenderer } from './types';
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

/**
 * Render a group of consecutive tool calls of the same name.
 *
 * - If the tool's renderer defines `renderGroup`, delegate to it.
 * - Otherwise fall back to `defaultRenderGroup`, wired with this registry's
 *   resolvers so per-tool `getDisplayName` / `renderCallArgs` / `renderResult`
 *   still apply.
 */
export function renderToolGroup(
	toolName: string,
	calls: ToolCallWithResult[],
	t: TFunction,
): ReactNode {
	const r = getRenderer(toolName);
	if (r.renderGroup) {
		return r.renderGroup(calls, t);
	}
	return defaultRenderGroup(calls, t, {
		getDisplayName: (call) => getDisplayName(call, t),
		renderCallArgs: (call) => renderCallArgs(call, t),
		renderResult: (call, result) => renderResult(call, result, t),
	});
}
