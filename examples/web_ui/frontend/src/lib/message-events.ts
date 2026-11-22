import { EventType, type AgentEvent } from '@agentscope-ai/agentscope/event';
import { appendEvent, type Msg } from '@agentscope-ai/agentscope/message';

/** Apply a streamed event to the reply consumed by the chat UI. */
export function appendMessageEvent(message: Msg, event: AgentEvent): Msg {
	appendEvent(message, event);
	if (event.type === EventType.REQUIRE_USER_CONFIRM && event.reply_id === message.id) {
		for (const call of event.tool_calls) {
			const block = message.content.find((b) => b.type === 'tool_call' && b.id === call.id);
			if (
				block &&
				'confirmation_message' in call &&
				typeof call.confirmation_message === 'string'
			) {
				Object.assign(block, { confirmation_message: call.confirmation_message });
			}
		}
	}
	if (event.type === EventType.REQUIRE_EXTERNAL_EXECUTION && event.reply_id === message.id) {
		// The SDK updates state only; the server may also have repaired the JSON input.
		for (const toolCall of event.tool_calls) {
			const block = message.content.find(
				(block) => block.type === 'tool_call' && block.id === toolCall.id,
			);
			if (block?.type === 'tool_call') block.input = toolCall.input;
		}
	}
	return message;
}
