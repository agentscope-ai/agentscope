import type { ToolCallBlock, ToolResultBlock } from '@agentscope-ai/agentscope/message';
import type { ReactNode } from 'react';

export type TFunction = (key: string, params?: Record<string, unknown>) => string;

export interface ToolCallWithResult {
	call: ToolCallBlock;
	result?: ToolResultBlock;
}

/**
 * A tool renderer only supplies *content*; the shared `ToolCallRow` owns the
 * collapsible shell, state icon, chevron and layout. Two render paths exist:
 *
 * - Inline row (`renderHeader` + `renderBody`): the trigger line and its
 *   expandable body. `renderBody` returning `null`/`undefined` makes the row
 *   non-expandable.
 * - Confirmation card (`getDisplayName` + `renderConfirmBody`): the title and
 *   body shown by `ConfirmCard` while a call awaits user approval.
 *
 * Every method is optional — `index.ts` falls back to the `Default*`
 * implementations when a tool doesn't override one.
 */
export interface ToolRenderer {
	getDisplayName?: (call: ToolCallBlock, t: TFunction) => string;
	renderConfirmBody?: (call: ToolCallBlock, t: TFunction) => ReactNode;
	/** Trigger-line content, laid out before the state icon / chevron. */
	renderHeader?: (pair: ToolCallWithResult, t: TFunction) => ReactNode;
	/** Expandable body; return `null`/`undefined` for a non-expandable row. */
	renderBody?: (pair: ToolCallWithResult, t: TFunction) => ReactNode;
}
