import { getContentBlocks, type Msg, type ToolCallBlock } from '@agentscope-ai/agentscope/message';

export type PendingHitlCall = {
	kind: 'confirmation' | 'external_execution';
	replyId: string;
	toolCall: ToolCallBlock;
};

/** Return unresolved HITL calls, newest message first. */
export function getPendingHitlCalls(messages: readonly Msg[]): PendingHitlCall[] {
	const pending: PendingHitlCall[] = [];
	for (let index = messages.length - 1; index >= 0; index -= 1) {
		const message = messages[index];
		if (message.role !== 'assistant') continue;
		const resultIds = new Set(
			getContentBlocks(message, 'tool_result').map((result) => result.id),
		);
		for (const toolCall of getContentBlocks(message, 'tool_call')) {
			if (resultIds.has(toolCall.id)) continue;
			if (toolCall.state === 'asking') {
				pending.push({ kind: 'confirmation', replyId: message.id, toolCall });
			} else if (toolCall.state === 'submitted') {
				pending.push({ kind: 'external_execution', replyId: message.id, toolCall });
			}
		}
	}
	return pending;
}
