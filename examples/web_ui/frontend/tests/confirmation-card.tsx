// Open /tests/confirmation-card.html on the frontend dev server.
import { EventType, type RequireUserConfirmEvent } from '@agentscope-ai/agentscope/event';
import type { Msg, ToolCallBlock } from '@agentscope-ai/agentscope/message';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { flushSync } from 'react-dom';
import { createRoot } from 'react-dom/client';

import { ChatContent } from '../src/components/chat/ChatContent';
import { SubagentHitlCard } from '../src/components/chat/SubagentHitlCard';
import { TooltipProvider } from '../src/components/ui/tooltip';
import { appendMessageEvent, confirmationId } from '../src/lib/message-events';

const root = createRoot(document.querySelector('#root')!);
const queryClient = new QueryClient();
const noop = async () => {};
const now = new Date().toISOString();
const call = {
	type: 'tool_call',
	id: 'one',
	name: 'preview',
	input: '{}',
	state: 'pending',
	created_at: now,
} as ToolCallBlock;

function verify(worker: boolean) {
	let message = {
		id: 'reply',
		role: 'assistant',
		name: 'agent',
		created_at: now,
		content: [{ ...call }],
	} as Msg;
	const answers: [string | undefined, boolean][] = [];
	function answer(tool: ToolCallBlock, confirmed: boolean) {
		answers.push([confirmationId(message, tool.id), confirmed]);
		return Promise.resolve();
	}
	let first: ToolCallBlock;
	for (const id of ['first', 'second']) {
		const asked = { ...call };
		const prompt: RequireUserConfirmEvent = {
			id,
			created_at: now,
			type: EventType.REQUIRE_USER_CONFIRM,
			reply_id: message.id,
			metadata: {},
			tool_calls: [asked],
		};
		appendMessageEvent(message, prompt);
		message = JSON.parse(JSON.stringify(message)) as Msg;
		if (id === 'first') first = message.content[0] as ToolCallBlock;
		else {
			const answer = { confirmed: true, tool_call: first!, request_id: 'first' };
			appendMessageEvent(message, {
				id: 'late-answer',
				created_at: now,
				type: EventType.USER_CONFIRM_RESULT,
				reply_id: message.id,
				confirm_results: [answer],
			});
		}

		flushSync(() =>
			root.render(
				<QueryClientProvider client={queryClient}>
					<TooltipProvider>
						{worker ? (
							<SubagentHitlCard
								entry={{
									event: { ...prompt },
									reply_id: message.id,
									worker_agent_id: 'worker',
									worker_agent_name: 'worker',
									worker_session_id: 'session',
									event_type: 'require_user_confirm',
									created_at: now,
								}}
								onResult={noop}
								onConfirm={answer}
							/>
						) : (
							<ChatContent
								msgs={[message]}
								phase="idle"
								queuedCount={0}
								queuedItems={[]}
								queueReorderDisabled={false}
								activeReplyId={null}
								disabled={false}
								onSend={noop}
								onUserConfirm={answer}
								onClientExternalToolResult={noop}
								onUpdateQueued={noop}
								onDeleteQueued={noop}
								onSteerQueued={noop}
								onMoveQueued={noop}
								onReorderQueued={noop}
								allowedInputTypes={[]}
								fileProcessor={async () => null}
								agentId={null}
								sessionId={null}
								cwd={null}
								onCwdChange={noop}
							/>
						)}
					</TooltipProvider>
				</QueryClientProvider>,
			),
		);
		const label = id === 'first' ? '1.' : '2.';
		const button = [...document.querySelectorAll('button')].find((b) =>
			b.textContent.trim().startsWith(label),
		);
		if (!button || button.disabled) throw Error('Expected an enabled button on ' + id);
		flushSync(() => button.click());
		if (!button.disabled) throw Error('Submitted card still enabled');
	}
	const denied = { tool_call: call, request_id: 'second', confirmed: false };
	const stale = { ...denied, request_id: 'first', confirmed: true };
	const duplicate = { ...denied, confirmed: true };
	appendMessageEvent(message, {
		id: 'batch',
		created_at: now,
		type: EventType.USER_CONFIRM_RESULT,
		reply_id: message.id,
		confirm_results: [stale, denied, duplicate],
	});
	const answered = message.content[0];
	if (
		answered.type !== 'tool_call' ||
		answered.state !== 'finished' ||
		confirmationId(message, call.id)
	)
		throw Error('First valid rejection was lost during batch replay');
	if (
		JSON.stringify(answers) !==
		JSON.stringify([
			['first', true],
			['second', false],
		])
	)
		throw Error('Unexpected answers: ' + JSON.stringify(answers));
}

function verifyMixedMemberCards() {
	const handled: string[] = [];
	const question = {
		header: 'Choice',
		question: 'Which?',
		options: ['A', 'B'].map((label) => ({ label, description: label })),
	};
	const tools = [
		call,
		{
			...call,
			id: 'question',
			name: 'AskUser',
			input: JSON.stringify({ questions: [question] }),
		},
		{ ...call, id: 'manual', name: 'client__choose_option' },
	];
	flushSync(() =>
		root.render(
			<>
				{tools.map((tool) => (
					<SubagentHitlCard
						key={tool.id}
						entry={{
							worker_session_id: 'worker',
							worker_agent_name: 'worker',
							worker_agent_id: 'a',
							reply_id: 'r',
							created_at: now,
							event: { id: tool.id, tool_calls: [tool] },
							event_type:
								tool === call
									? 'require_user_confirm'
									: 'require_external_execution',
						}}
						onConfirm={async () => {
							handled.push('approval');
						}}
						onResult={async (submitted) => {
							handled.push(submitted.id);
						}}
					/>
				))}
			</>,
		),
	);
	const enter = () =>
		new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true });
	const manual = document.querySelector<HTMLTextAreaElement>('section:has(details) textarea');
	const option = document.querySelector<HTMLButtonElement>('[role="radio"]');
	if (!manual || !option) throw Error('Missing member tool form');
	manual.focus();
	if (!manual.dispatchEvent(enter())) throw Error('Manual form Enter was intercepted');
	option.focus();
	flushSync(() => option.dispatchEvent(enter()));
	if (JSON.stringify(handled) !== '["question"]') throw Error('Unexpected callbacks: ' + handled);
}

try {
	verify(false);
	verify(true);
	verifyMixedMemberCards();
	document.querySelector('output')!.textContent = 'PASS';
} catch (error) {
	document.querySelector('output')!.textContent = String(error);
	throw error;
}
