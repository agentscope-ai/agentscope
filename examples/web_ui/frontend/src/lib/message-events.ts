import { EventType, type AgentEvent } from '@agentscope-ai/agentscope/event';
import { appendEvent, type Msg } from '@agentscope-ai/agentscope/message';

export function confirmationId(message: Msg, callId: string): string | undefined {
	return (message.metadata?.pending_confirmations as Record<string, string> | undefined)?.[
		callId
	];
}

function setConfirmation(message: Msg, callId: string, requestId?: string) {
	if (!requestId && !confirmationId(message, callId)) return;
	const requests = { ...(message.metadata?.pending_confirmations as Record<string, string>) };
	if (requestId) requests[callId] = requestId;
	else delete requests[callId];
	message.metadata ??= {};
	if (Object.keys(requests).length) message.metadata.pending_confirmations = requests;
	else delete message.metadata.pending_confirmations;
}

/** Apply a streamed event to the reply consumed by the chat UI. */
export function appendMessageEvent(message: Msg, event: AgentEvent): Msg {
	if ('reply_id' in event && event.reply_id !== message.id) return message;
	if (event.type === EventType.USER_CONFIRM_RESULT) {
		// Filter before the SDK changes the call state. A late answer must
		// leave a newer card for the same call clickable.
		const pending = new Map(
			message.content.flatMap((block) =>
				block.type === 'tool_call' && block.state === 'asking'
					? [[block.id, confirmationId(message, block.id)] as const]
					: [],
			),
		);
		event = {
			...event,
			confirm_results: event.confirm_results.filter((result) => {
				const call = result.tool_call;
				const id = 'request_id' in result ? result.request_id : undefined;
				return id !== undefined && pending.get(call.id) === id && pending.delete(call.id);
			}),
		};
	}
	appendEvent(message, event);

	function updateCall(id: string, name: string, input: string) {
		const index = message.content.findIndex(
			(item) => item.type === 'tool_call' && item.id === id,
		);
		const block = message.content[index];
		if (block?.type === 'tool_call') {
			message.content[index] = {
				...block,
				name,
				input,
			};
		}
	}

	if (
		event.type === EventType.REQUIRE_USER_CONFIRM ||
		event.type === EventType.REQUIRE_EXTERNAL_EXECUTION
	) {
		for (const call of event.tool_calls) {
			updateCall(call.id, call.name, call.input);
			setConfirmation(
				message,
				call.id,
				event.type === EventType.REQUIRE_USER_CONFIRM ? event.id : undefined,
			);
		}
	} else if (event.type === EventType.USER_CONFIRM_RESULT) {
		for (const { confirmed, tool_call: call } of event.confirm_results) {
			setConfirmation(message, call.id);
			if (confirmed) updateCall(call.id, call.name, call.input);
		}
	} else if (event.type === EventType.TOOL_RESULT_END) {
		setConfirmation(message, event.tool_call_id);
	} else if (
		event.type === EventType.TOOL_RESULT_START &&
		'tool_call_input' in event &&
		typeof event.tool_call_input === 'string'
	) {
		updateCall(event.tool_call_id, event.tool_call_name, event.tool_call_input);
	}
	return message;
}
