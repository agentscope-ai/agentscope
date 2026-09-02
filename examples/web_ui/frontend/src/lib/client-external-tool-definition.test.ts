import assert from 'node:assert/strict';
import test from 'node:test';

import {
	LEGACY_REQUEST_USER_INPUT_TOOL_NAME,
	REQUEST_USER_INPUT_EXTERNAL_TOOL,
	REQUEST_USER_INPUT_TOOL_NAME,
} from './request-user-input.ts';

test('RequestUserInput uses the reserved client namespace', () => {
	assert.equal(REQUEST_USER_INPUT_TOOL_NAME, 'client__request_user_input');
	assert.equal(REQUEST_USER_INPUT_EXTERNAL_TOOL.name, REQUEST_USER_INPUT_TOOL_NAME);
	assert.equal(REQUEST_USER_INPUT_EXTERNAL_TOOL.read_only, true);
	assert.equal(LEGACY_REQUEST_USER_INPUT_TOOL_NAME, 'RequestUserInput');
});

test('RequestUserInput exposes an object input schema', () => {
	assert.deepEqual(
		{
			type: REQUEST_USER_INPUT_EXTERNAL_TOOL.input_schema.type,
			required: REQUEST_USER_INPUT_EXTERNAL_TOOL.input_schema.required,
		},
		{
			type: 'object',
			required: ['question', 'options'],
		},
	);
});
