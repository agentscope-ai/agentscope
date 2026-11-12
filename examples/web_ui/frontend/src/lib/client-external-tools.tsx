import type { ContentBlock, ToolCallBlock } from '@agentscope-ai/agentscope/message';
import type { ComponentType } from 'react';

import { AskUserCard } from '@/components/chat/AskUserCard';
import { AskUserRenderer } from '@/components/chat/tool-renderers/AskUserRenderer';
import type { ToolRenderer } from '@/components/chat/tool-renderers/types';
import { ASK_USER_TOOL_NAME, parseAskUserPayload, toAskUserComposerResult } from '@/lib/ask-user';

export type ClientExternalToolResult =
	| string
	| number
	| boolean
	| null
	| ClientExternalToolResult[]
	| { [key: string]: ClientExternalToolResult };

export type PendingClientExternalToolProps = {
	toolCall: ToolCallBlock;
	onSubmit: (result: ClientExternalToolResult) => Promise<void>;
	onCancel?: () => void;
	cancelling: boolean;
};

export type ClientExternalToolRegistration = {
	name: string;
	displayNameKey: string;
	displayDescriptionKey: string;
	PendingComponent: ComponentType<PendingClientExternalToolProps>;
	canCompose?: (toolCall: ToolCallBlock) => boolean;
	composerResult?: (
		blocks: ContentBlock[],
		toolCall: ToolCallBlock,
	) => ClientExternalToolResult | null;
	historyRenderer?: ToolRenderer;
};

export const CLIENT_EXTERNAL_TOOL_REGISTRY: readonly ClientExternalToolRegistration[] = [
	{
		name: ASK_USER_TOOL_NAME,
		displayNameKey: 'clientTools.askUser.name',
		displayDescriptionKey: 'clientTools.askUser.description',
		PendingComponent: AskUserCard,
		canCompose: (toolCall) => parseAskUserPayload(toolCall.input)?.questions.length === 1,
		composerResult: (blocks, toolCall) => toAskUserComposerResult(blocks, toolCall.input),
		historyRenderer: AskUserRenderer,
	},
];

const registrationsByName = new Map(
	CLIENT_EXTERNAL_TOOL_REGISTRY.map((registration) => [registration.name, registration]),
);

export function getClientExternalToolRegistration(
	toolName: string,
): ClientExternalToolRegistration | undefined {
	return registrationsByName.get(toolName);
}
