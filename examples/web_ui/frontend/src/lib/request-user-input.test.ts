/// <reference types="node" />

import assert from 'node:assert/strict';
import test from 'node:test';

import {
	getInitialRadioIndex,
	getNextRadioIndex,
	getRadioEnterAction,
	parseRequestUserInputPayload,
	parseRequestUserInputResult,
	shouldSubmitOtherInput,
} from './request-user-input.ts';

test('parseRequestUserInputPayload rejects malformed structures', () => {
	const actual = [
		parseRequestUserInputPayload('{invalid'),
		parseRequestUserInputPayload('{"question":"Missing options"}'),
		parseRequestUserInputPayload('{"question":"Choose","options":[{"label":"One"},{}]}'),
		parseRequestUserInputPayload(
			'{"question":"Choose","options":[{"label":"One"},{"label":"Two"}]}',
		),
	];

	assert.deepEqual(actual, [
		null,
		null,
		null,
		{
			question: 'Choose',
			options: [{ label: 'One' }, { label: 'Two' }],
		},
	]);
});

test('parseRequestUserInputResult returns only readable answers', () => {
	const actual = [
		parseRequestUserInputResult('{invalid'),
		parseRequestUserInputResult('{"type":"other","text":"  Custom  "}'),
		parseRequestUserInputResult('{"type":"option","option_index":1,"label":"Complete"}'),
	];

	assert.deepEqual(actual, [
		null,
		{ type: 'other', text: 'Custom' },
		{ type: 'option', option_index: 1, label: 'Complete' },
	]);
});

test('getInitialRadioIndex prefers the recommended option', () => {
	const actual = [
		getInitialRadioIndex([]),
		getInitialRadioIndex([{}, {}]),
		getInitialRadioIndex([{}, { recommended: true }, {}]),
	];

	assert.deepEqual(actual, [null, 0, 1]);
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

test('getRadioEnterAction submits options and edits an empty Other answer', () => {
	const actual = [
		getRadioEnterAction(0, 2, ''),
		getRadioEnterAction(2, 2, ''),
		getRadioEnterAction(2, 2, 'Custom answer'),
	];

	assert.deepEqual(actual, ['submit', 'edit-other', 'submit']);
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
