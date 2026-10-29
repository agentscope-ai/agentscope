/// <reference types="node" />

import assert from 'node:assert/strict';
import test from 'node:test';

import {
	formatAskUserOutput,
	getNextRadioIndex,
	shouldSubmitOtherInput,
	parseAskUserPayload,
	parseAskUserResult,
	toAskUserComposerResult,
} from './ask-user.ts';
import {
	parseClientExternalToolStoreState,
	getEnabledClientBuiltinTools,
	getEnabledClientExternalToolDefinitions,
} from './client-external-tool-store.ts';

const question = {
	question: 'Which approach?',
	header: 'Approach',
	context: 'Compare these.',
	options: [
		{ label: 'A', description: 'First', preview: 'a()' },
		{ label: 'B', description: 'Second' },
	],
};

test('native payload handles batches, multi-select and rejects malformed inputs', () => {
	const value = {
		questions: [question, { ...question, question: 'Which features?', multi_select: true }],
	};
	assert.deepEqual(parseAskUserPayload(JSON.stringify(value)), value);
	for (const invalid of [
		'{',
		'{}',
		JSON.stringify({ questions: [question, question] }),
		JSON.stringify({
			questions: [{ ...question, options: [question.options[0], question.options[0]] }],
		}),
	]) {
		assert.equal(parseAskUserPayload(invalid), null);
	}
});

test('metadata and readable output preserve all answers', () => {
	const result = {
		answers: [{ question: question.question, selected: ['A', 'B'], other: 'Custom' }],
	};
	assert.deepEqual(parseAskUserResult(result), result);
	assert.equal(formatAskUserOutput(result), 'Which approach?\nA, B, Custom');
	assert.equal(parseAskUserResult({ answers: [{ selected: 'A' }] }), null);
});

test('composer answers exactly one native question, never guesses a batch target', () => {
	const blocks = [
		{ type: 'text' as const, text: ' My plan ', id: 't', created_at: '', finished_at: '' },
	];
	assert.deepEqual(toAskUserComposerResult(blocks, JSON.stringify({ questions: [question] })), {
		answers: [{ question: question.question, selected: [], other: 'My plan' }],
	});
	assert.equal(
		toAskUserComposerResult(
			blocks,
			JSON.stringify({ questions: [question, { ...question, question: 'Next?' }] }),
		),
		null,
	);
});

test('native selections preserve disabled sessions and stay separate from custom schemas', () => {
	const state = parseClientExternalToolStoreState(
		JSON.stringify({
			version: 1,
			customTools: [],
			selections: {
				'["a","s"]': ['AskUser'],
				'["a","off"]': [],
			},
		}),
	);
	assert.deepEqual(state.selections, { '["a","s"]': ['AskUser'], '["a","off"]': [] });
	assert.deepEqual(getEnabledClientBuiltinTools('a', 's', state), ['AskUser']);
	assert.deepEqual(getEnabledClientBuiltinTools('a', 'off', state), []);
	assert.deepEqual(getEnabledClientExternalToolDefinitions('a', 's', state), []);
});

test('getNextRadioIndex follows radio-group keyboard navigation', () => {
	const actual = [
		['ArrowDown', 0],
		['ArrowRight', 1],
		['ArrowDown', 2],
		['ArrowUp', 0],
		['ArrowLeft', 2],
		['Home', 2],
		['End', 0],
		['Enter', 1],
	].map(([key, currentIndex]) => getNextRadioIndex(currentIndex as number, key as string, 3));

	assert.deepEqual(actual, [1, 2, 0, 2, 1, 0, 2, null]);
});

test('shouldSubmitOtherInput matches the main composer Enter behavior', () => {
	const actual = [
		shouldSubmitOtherInput('Enter', false, false),
		shouldSubmitOtherInput('Enter', true, false),
		shouldSubmitOtherInput('Enter', false, true),
		shouldSubmitOtherInput('Escape', false, false),
	];

	assert.deepEqual(actual, [true, false, false, false]);
});
