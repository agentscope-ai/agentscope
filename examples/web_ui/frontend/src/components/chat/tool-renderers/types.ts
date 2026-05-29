import type { ToolCallBlock, ToolResultBlock } from '@agentscope-ai/agentscope/message';
import type { ReactNode } from 'react';

export type TFunction = (key: string, params?: Record<string, unknown>) => string;

export interface ToolCallWithResult {
	call: ToolCallBlock;
	result?: ToolResultBlock;
}

export interface ToolRenderer {
	getDisplayName?: (call: ToolCallBlock, t: TFunction) => string;
	renderCallArgs?: (call: ToolCallBlock, t: TFunction) => ReactNode;
	renderResult?: (call: ToolCallBlock, result: ToolResultBlock, t: TFunction) => ReactNode;
	renderConfirmBody?: (call: ToolCallBlock, t: TFunction) => ReactNode;
	/**
	 * Render a group of consecutive tool calls of the same name.
	 * Receives the visible (non-truncated) calls — `MessageBubble` truncates
	 * at the first `asking` call before invoking, and renders ConfirmCard
	 * separately.
	 */
	renderGroup?: (calls: ToolCallWithResult[], t: TFunction) => ReactNode;
}
