/// <reference types="node" />

import assert from 'node:assert/strict';
import test from 'node:test';

import { CLIENT_BUILTIN_TOOL_NAMES } from './client-external-tool-definitions.ts';

test('new calls declare the native tool without duplicating its schema', () => {
	assert.deepEqual(CLIENT_BUILTIN_TOOL_NAMES, ['AskUser']);
});
