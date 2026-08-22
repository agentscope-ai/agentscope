import type { ContentBlock, ToolCallBlock } from '@agentscope-ai/agentscope/message';
import type { ComponentType } from 'react';

import type { ClientExternalToolDefinition } from '@/api/types';
import { PendingRequestUserInputCard } from '@/components/chat/PendingRequestUserInputCard';
import { RequestUserInputRenderer } from '@/components/chat/tool-renderers/RequestUserInputRenderer';
import type { ToolRenderer } from '@/components/chat/tool-renderers/types';
import {
	LEGACY_REQUEST_USER_INPUT_TOOL_NAME,
	REQUEST_USER_INPUT_EXTERNAL_TOOL,
	toOtherUserInputResult,
} from '@/lib/request-user-input';

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
	definition: ClientExternalToolDefinition;
	displayNameKey: string;
	displayDescriptionKey: string;
	legacyNames?: readonly string[];
	PendingComponent: ComponentType<PendingClientExternalToolProps>;
	composerResult?: (blocks: ContentBlock[]) => ClientExternalToolResult | null;
	historyRenderer?: ToolRenderer;
};

export const CLIENT_EXTERNAL_TOOL_REGISTRY: readonly ClientExternalToolRegistration[] = [
	{
		definition: REQUEST_USER_INPUT_EXTERNAL_TOOL,
		displayNameKey: 'clientTools.requestUserInput.name',
		displayDescriptionKey: 'clientTools.requestUserInput.description',
		legacyNames: [LEGACY_REQUEST_USER_INPUT_TOOL_NAME],
		PendingComponent: PendingRequestUserInputCard,
		composerResult: toOtherUserInputResult,
		historyRenderer: RequestUserInputRenderer,
	},
];

const registrationsByName = new Map(
	CLIENT_EXTERNAL_TOOL_REGISTRY.flatMap((registration) =>
		[registration.definition.name, ...(registration.legacyNames ?? [])].map(
			(name) => [name, registration] as const,
		),
	),
);

export function getClientExternalToolRegistration(
	toolName: string,
): ClientExternalToolRegistration | undefined {
	return registrationsByName.get(toolName);
}
