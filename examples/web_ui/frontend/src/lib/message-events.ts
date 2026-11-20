import { EventType, type AgentEvent } from '@agentscope-ai/agentscope/event';
import { appendEvent, type Base64Source, type Msg } from '@agentscope-ai/agentscope/message';

// Only a chunk's final one or two bytes can affect the next base64 quartet.
// Keep the aligned prefix separate so subsequent deltas never decode it.
const audioTails = new WeakMap<Base64Source, { prefix: string; tail: string; value: string }>();

function appendAudioChunk(source: Base64Source, encoded: string): void {
	let stream = audioTails.get(source);
	if (!stream || stream.value !== source.data) {
		const existing = atob(source.data);
		const aligned = existing.length - (existing.length % 3);
		stream = {
			prefix: btoa(existing.slice(0, aligned)),
			tail: existing.slice(aligned),
			value: source.data,
		};
		audioTails.set(source, stream);
	}
	const bytes = stream.tail + atob(encoded);
	const aligned = bytes.length - (bytes.length % 3);
	stream.prefix += btoa(bytes.slice(0, aligned));
	stream.tail = bytes.slice(aligned);
	source.data = stream.value = stream.prefix + btoa(stream.tail);
}

/** Apply a streamed event to the reply consumed by the chat UI. */
export function appendMessageEvent(message: Msg, event: AgentEvent): Msg {
	if (
		event.type === EventType.DATA_BLOCK_DELTA &&
		event.media_type.startsWith('audio/') &&
		event.reply_id === message.id &&
		event.data
	) {
		const block = message.content.find(
			(item) => item.type === 'data' && item.id === event.block_id,
		);
		if (block?.type === 'data' && block.source.type === 'base64') {
			appendAudioChunk(block.source, event.data);
			return message;
		}
	}
	appendEvent(message, event);
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
