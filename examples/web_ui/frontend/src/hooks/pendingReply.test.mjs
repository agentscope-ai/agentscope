import assert from 'node:assert/strict';
import test from 'node:test';

import {
	findPendingReply,
	latestMessageVersions,
	sessionStatusHasActiveReply,
} from './pendingReply.ts';

test('only non-idle server statuses keep a reply active', () => {
	assert.equal(sessionStatusHasActiveReply('idle'), false);
	assert.equal(sessionStatusHasActiveReply('running'), true);
	assert.equal(sessionStatusHasActiveReply('awaiting_permission'), true);
	assert.equal(sessionStatusHasActiveReply('awaiting_external_result'), true);
});

test('finds a parked reply even after a rejected user turn and setup error', () => {
	const parked = {
		id: 'parked',
		role: 'assistant',
		finished_reason: null,
		content: [{ type: 'tool_call', id: 'call', state: 'asking' }],
	};
	const messages = [
		parked,
		{ id: 'later-user', role: 'user', content: [] },
		{ id: 'setup-error', role: 'assistant', content: [], finished_reason: 'error' },
	];

	assert.equal(findPendingReply(messages), parked);
});

test('ignores a reply after its pending call is resolved', () => {
	const resolved = {
		id: 'resolved',
		role: 'assistant',
		finished_reason: 'completed',
		content: [{ type: 'tool_call', id: 'call', state: 'finished' }],
	};
	assert.equal(findPendingReply([resolved]), undefined);
});

test('preserves the newest pending reply while ignoring an older one', () => {
	const older = {
		id: 'older',
		role: 'assistant',
		content: [{ type: 'tool_call', state: 'asking' }],
	};
	const newer = {
		id: 'newer',
		role: 'assistant',
		content: [{ type: 'tool_call', state: 'submitted' }],
	};
	assert.equal(findPendingReply([older, newer]), newer);
});

test('newer completed version of the same reply supersedes an old asking version', () => {
	const parked = {
		id: 'same-reply',
		role: 'assistant',
		finished_reason: null,
		content: [{ type: 'tool_call', id: 'call', state: 'asking' }],
	};
	const completed = {
		id: 'same-reply',
		role: 'assistant',
		finished_reason: 'error',
		content: [
			{ type: 'tool_call', id: 'call', state: 'finished' },
			{ type: 'tool_result', id: 'call', state: 'success' },
		],
	};
	assert.equal(
		findPendingReply([parked, { id: 'later-user', role: 'user', content: [] }, completed]),
		undefined,
	);
});

test('history renders only the latest version of a duplicated reply', () => {
	const oldReply = { id: 'reply', role: 'assistant', content: [], finished_reason: null };
	const user = { id: 'user', role: 'user', content: [] };
	const finalReply = { id: 'reply', role: 'assistant', content: [], finished_reason: 'error' };
	assert.deepEqual(latestMessageVersions([oldReply, user, finalReply]), [finalReply, user]);
});
