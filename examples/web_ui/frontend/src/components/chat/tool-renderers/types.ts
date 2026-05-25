import type { ToolCallBlock, ToolResultBlock } from '@agentscope-ai/agentscope/message';
import type { ReactNode } from 'react';

export type TFunction = (key: string, params?: Record<string, unknown>) => string;

export interface ToolRenderer {
	getDisplayName?: (call: ToolCallBlock, t: TFunction) => string;
	renderCallArgs?: (call: ToolCallBlock, t: TFunction) => ReactNode;
	renderResult?: (call: ToolCallBlock, result: ToolResultBlock, t: TFunction) => ReactNode;
	renderConfirmBody?: (call: ToolCallBlock, t: TFunction) => ReactNode;
}
