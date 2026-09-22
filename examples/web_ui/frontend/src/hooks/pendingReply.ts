import type { Msg, ToolCallBlock } from '@agentscope-ai/agentscope/message';

/** Replace older copies of a reply while preserving its original position. */
export function latestMessageVersions(messages: Msg[]): Msg[] {
	const result: Msg[] = [];
	const indexById = new Map<string, number>();
	for (const message of messages) {
		const index = indexById.get(message.id);
		if (index === undefined) {
			indexById.set(message.id, result.length);
			result.push(message);
		} else {
			result[index] = message;
		}
	}
	return result;
}

/** Find the assistant reply currently waiting on a tool result. */
export function findPendingReply(messages: Msg[]): Msg | undefined {
	const seenReplyIds = new Set<string>();
	for (let i = messages.length - 1; i >= 0; i--) {
		const message = messages[i];
		if (message.role !== 'assistant' || seenReplyIds.has(message.id)) continue;
		seenReplyIds.add(message.id);
		if (message.finished_reason != null) continue;
		if (
			message.content.some(
				(block) =>
					block.type === 'tool_call' &&
					((block as ToolCallBlock).state === 'asking' ||
						(block as ToolCallBlock).state === 'submitted'),
			)
		) {
			return message;
		}
	}
	return undefined;
}
