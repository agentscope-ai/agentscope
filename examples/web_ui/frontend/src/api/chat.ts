import { client } from './client';
import type { AgentEvent, ChatRequest } from './types';

export const chatApi = {
	stream: async function* (body: ChatRequest, signal?: AbortSignal): AsyncGenerator<AgentEvent> {
		const res = await client.stream('/chat/', { method: 'POST', body, signal });

		const reader = res.body!.getReader();
		const decoder = new TextDecoder();
		let buffer = '';

		while (true) {
			const { done, value } = await reader.read();
			if (done) break;

			buffer += decoder.decode(value, { stream: true });
			const lines = buffer.split('\n');
			buffer = lines.pop() ?? '';

			for (const line of lines) {
				if (line.startsWith('data: ')) {
					const json = line.slice(6).trim();
					if (json) yield JSON.parse(json) as AgentEvent;
				}
			}
		}
	},
};
