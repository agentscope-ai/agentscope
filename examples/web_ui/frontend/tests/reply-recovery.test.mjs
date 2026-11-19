import assert from 'node:assert/strict';
import test from 'node:test';

import {
	canonicalRepliesReady,
	createPartialReply,
	findReplyById,
	loadReplyCheckpoint,
	mergeHistoryWithPendingUsers,
	waitForAbortableDelay,
} from '../src/lib/reply-recovery.ts';

const createdAt = '2026-01-01T00:00:00.000Z';

function message(id, role = 'assistant', text = '') {
	return {
		id,
		name: role,
		role,
		content: text ? [{ type: 'text', id: `${id}-text`, text, created_at: createdAt }] : [],
		metadata: {},
		created_at: createdAt,
	};
}

function event(type, extra = {}) {
	return { id: 'synthetic-event', type, created_at: createdAt, ...extra };
}

test('recovering a text delta creates an identified partial reply without fabricating a text block', () => {
	const input = event('TEXT_BLOCK_DELTA', {
		reply_id: 'reply-text',
		block_id: 'missing-text-start',
		delta: 'Only the suffix reached the replay buffer.',
	});
	const original = structuredClone(input);
	const partial = createPartialReply(input);

	assert.ok(partial);
	assert.equal(partial.id, 'reply-text');
	assert.equal(partial.metadata.partial_replay, true);
	assert.deepEqual(partial.content, []);
	assert.equal(partial.finished_at ?? null, null);
	assert.deepEqual(input, original, 'recovery must not rewrite the received event');
});

test('recovering a tool delta does not invent the missing tool name or argument prefix', () => {
	const partial = createPartialReply(
		event('TOOL_CALL_DELTA', {
			reply_id: 'reply-tool',
			tool_call_id: 'missing-tool-start',
			delta: '"untrusted suffix"}',
		}),
	);

	assert.ok(partial);
	assert.equal(partial.id, 'reply-tool');
	assert.equal(partial.metadata.partial_replay, true);
	assert.deepEqual(partial.content, []);
});

test('an event without reply identity cannot create a phantom reply', () => {
	assert.equal(createPartialReply(event('CUSTOM', { name: 'team_updated', value: {} })), null);
});

test('an empty reply identity cannot create a phantom reply', () => {
	assert.equal(
		createPartialReply(event('MODEL_CALL_START', { reply_id: '', model_name: 'test' })),
		null,
	);
});

test('snapshot lookup returns the exact current history object for a resumed reply', () => {
	const detachedReply = message('resumed-reply', 'assistant', 'old client content');
	const canonicalReply = message('resumed-reply', 'assistant', 'persisted content');
	const history = [message('user-turn', 'user'), canonicalReply];
	const rebound = findReplyById(history, detachedReply.id);

	assert.strictEqual(rebound, canonicalReply);
	assert.notStrictEqual(rebound, detachedReply);
	assert.strictEqual(history[1], canonicalReply);
});

test('missing reply ids do not fall back to another reply or the list tail', () => {
	const history = [message('assistant-tail')];
	assert.equal(findReplyById(history, 'absent'), null);
	assert.equal(findReplyById(history, undefined), null);
	assert.equal(findReplyById(history, null), null);
	assert.equal(findReplyById([], 'absent'), null);
});

test('reconnect preserves a submitted user turn until the server persists it', () => {
	const persisted = message('history-user', 'user', 'earlier turn');
	const pending = message('pending-user', 'user', 'new submitted turn');
	const restored = mergeHistoryWithPendingUsers([persisted], new Map([[pending.id, pending]]));

	assert.deepEqual(
		restored.map((item) => item.id),
		['history-user', 'pending-user'],
	);
	assert.strictEqual(restored[0], persisted);
	assert.strictEqual(restored[1], pending);
});

test('a persisted user message wins over its optimistic copy without duplicate ids', () => {
	const pending = message('user-turn', 'user', 'optimistic text');
	const canonical = message('user-turn', 'user', 'canonical text');
	const restored = mergeHistoryWithPendingUsers([canonical], new Map([[pending.id, pending]]));

	assert.equal(restored.length, 1);
	assert.strictEqual(restored[0], canonical);
	assert.equal(restored[0].content[0].text, 'canonical text');
});

test('multiple pending turns keep insertion order and do not duplicate persisted turns', () => {
	const first = message('first', 'user');
	const second = message('second', 'user');
	const third = message('third', 'user');
	const canonicalSecond = message('second', 'user', 'accepted');
	const restored = mergeHistoryWithPendingUsers(
		[first, canonicalSecond],
		new Map([
			[second.id, second],
			[third.id, third],
		]),
	);

	assert.deepEqual(
		restored.map((item) => item.id),
		['first', 'second', 'third'],
	);
	assert.strictEqual(restored[1], canonicalSecond);
});

test('history reconciliation does not mutate the history array or pending map', () => {
	const canonical = message('canonical', 'assistant');
	const pending = message('pending', 'user');
	const history = Object.freeze([canonical]);
	const pendingUsers = new Map([[pending.id, pending]]);
	const restored = mergeHistoryWithPendingUsers(history, pendingUsers);

	assert.notStrictEqual(restored, history);
	assert.deepEqual(history, [canonical]);
	assert.equal(pendingUsers.size, 1);
	assert.strictEqual(pendingUsers.get(pending.id), pending);
});

test('canonical completion replaces a partial reply and its replay warning', () => {
	const partial = createPartialReply(
		event('REPLY_END', {
			reply_id: 'completed-reply',
			finished_reason: 'completed',
		}),
	);
	assert.ok(partial);
	assert.equal(partial.metadata.partial_replay, true);
	const canonical = {
		...message('completed-reply', 'assistant', 'The complete saved response.'),
		finished_at: '2026-01-01T00:00:01.000Z',
		finished_reason: 'completed',
	};
	const pending = message('next-user-turn', 'user');
	const restored = mergeHistoryWithPendingUsers([canonical], new Map([[pending.id, pending]]));
	const rebound = findReplyById(restored, partial.id);

	assert.strictEqual(rebound, canonical);
	assert.notStrictEqual(rebound, partial);
	assert.equal(rebound.metadata.partial_replay, undefined);
	assert.equal(rebound.content[0].text, 'The complete saved response.');
	assert.equal(rebound.finished_reason, 'completed');
	assert.deepEqual(
		restored.map((item) => item.id),
		['completed-reply', 'next-user-turn'],
	);
});

test('canonical recovery requires every requested reply to be complete and non-partial', () => {
	const finished = { ...message('finished'), finished_at: createdAt };
	const unfinished = message('unfinished');
	const partial = {
		...message('partial'),
		finished_at: createdAt,
		metadata: { partial_replay: true },
	};

	assert.equal(canonicalRepliesReady([], new Set()), true);
	assert.equal(canonicalRepliesReady([finished], new Set(['finished'])), true);
	assert.equal(canonicalRepliesReady([finished], new Set(['missing'])), false);
	assert.equal(canonicalRepliesReady([unfinished], new Set(['unfinished'])), false);
	assert.equal(canonicalRepliesReady([partial], new Set(['partial'])), false);
	assert.equal(
		canonicalRepliesReady([finished, unfinished], new Set(['finished', 'unfinished'])),
		false,
	);
});

test('canonical recovery accepts a fully persisted set of completed replies', () => {
	const replies = ['first', 'second'].map((id) => ({ ...message(id), finished_at: createdAt }));
	assert.equal(canonicalRepliesReady(replies, new Set(['first', 'second'])), true);
});

test('checkpoint recovery waits for persistence and returns the complete canonical snapshot', async () => {
	const reply = {
		...message('recover-me', 'assistant', 'Complete saved response.'),
		finished_at: createdAt,
	};
	const snapshots = [
		{ messages: [], event_cursor: null, is_running: true },
		{ messages: [message('recover-me')], event_cursor: null, is_running: true },
		{ messages: [reply], event_cursor: 'saved-checkpoint', is_running: false },
	];
	let reads = 0;
	const recovered = await loadReplyCheckpoint(
		async () => snapshots[reads++],
		new Set(['recover-me']),
		new AbortController().signal,
		[0, 0, 0, 0],
	);

	assert.equal(reads, 3, 'stop polling once the complete reply has been persisted');
	assert.strictEqual(recovered, snapshots[2]);
	assert.strictEqual(findReplyById(recovered.messages, 'recover-me'), reply);
	assert.equal(recovered.event_cursor, 'saved-checkpoint');
});

test('checkpoint recovery does not reread a snapshot that is already canonical', async () => {
	const snapshot = { messages: [{ ...message('ready'), finished_at: createdAt }] };
	let reads = 0;
	const recovered = await loadReplyCheckpoint(
		async () => {
			reads += 1;
			return snapshot;
		},
		new Set(['ready']),
		new AbortController().signal,
		[0, 0, 0],
	);

	assert.equal(reads, 1);
	assert.strictEqual(recovered, snapshot);
});

test('checkpoint recovery is bounded when the canonical message never appears', async () => {
	const snapshots = [{ messages: [] }, { messages: [] }, { messages: [] }];
	let reads = 0;
	await assert.rejects(
		loadReplyCheckpoint(
			async () => snapshots[reads++],
			new Set(['not-persisted']),
			new AbortController().signal,
			[0, 0, 0],
		),
		/not yet available in saved history/,
	);

	assert.equal(reads, 3);
});

test('slow persistence keeps recovery pending until a later pass can return the saved reply', async () => {
	const replyIds = new Set(['slow-reply']);
	const controller = new AbortController();
	await assert.rejects(
		loadReplyCheckpoint(async () => ({ messages: [] }), replyIds, controller.signal, [0]),
		/not yet available in saved history/,
	);
	assert.equal(replyIds.has('slow-reply'), true);
	const saved = {
		messages: [
			{
				...message('slow-reply', 'assistant', 'Complete saved response.'),
				finished_at: createdAt,
			},
		],
		event_cursor: 'saved-checkpoint',
	};
	const recovered = await loadReplyCheckpoint(
		async () => saved,
		replyIds,
		controller.signal,
		[0],
	);
	assert.strictEqual(recovered, saved);
	assert.equal(canonicalRepliesReady(recovered.messages, replyIds), true);
});

test('checkpoint recovery stops when the user changes sessions before the first read', async () => {
	const controller = new AbortController();
	controller.abort();
	let reads = 0;
	await assert.rejects(
		loadReplyCheckpoint(
			async () => {
				reads += 1;
				return { messages: [] };
			},
			new Set(['not-persisted']),
			controller.signal,
			[0, 0],
		),
		{ name: 'AbortError' },
	);
	assert.equal(reads, 0);
});

test(
	'checkpoint recovery does not perform later reads after cancellation',
	{ timeout: 1_000 },
	async () => {
		const controller = new AbortController();
		let reads = 0;
		await assert.rejects(
			loadReplyCheckpoint(
				async () => {
					reads += 1;
					controller.abort();
					return { messages: [] };
				},
				new Set(['not-persisted']),
				controller.signal,
				[0, 30_000],
			),
			{ name: 'AbortError' },
		);
		assert.equal(reads, 1);
	},
);

test('an already aborted reconnect delay rejects with AbortError', async () => {
	const controller = new AbortController();
	controller.abort();
	await assert.rejects(waitForAbortableDelay(30_000, controller.signal), { name: 'AbortError' });
});

test(
	'switching sessions settles and cancels a pending reconnect delay',
	{ timeout: 1_000 },
	async () => {
		const controller = new AbortController();
		const pendingDelay = waitForAbortableDelay(30_000, controller.signal);
		controller.abort();
		await assert.rejects(pendingDelay, { name: 'AbortError' });
	},
);

test('a completed reconnect delay resolves and remains completed after later abort', async () => {
	const controller = new AbortController();
	const pendingDelay = waitForAbortableDelay(1, controller.signal);
	await assert.doesNotReject(pendingDelay);
	controller.abort();
	await assert.doesNotReject(pendingDelay);
});
