import assert from 'node:assert/strict';
import test from 'node:test';

import {
	clearClientExternalToolSelection,
	getEnabledClientExternalToolDefinitions,
	getEnabledClientExternalToolNames,
	normalizeClientToolName,
	parseClientExternalToolStoreState,
	setClientExternalToolEnabled,
	validateCustomClientToolDraft,
} from './client-external-tool-store.ts';

const VALID_DRAFT = {
	displayName: 'Collect feedback',
	name: 'collect_feedback',
	description: 'Collect structured feedback from the user.',
	inputSchema: JSON.stringify({
		type: 'object',
		properties: { score: { type: 'number' } },
		required: ['score'],
	}),
};

test('normalizes custom tool names into the reserved namespace', () => {
	assert.equal(normalizeClientToolName(' collect_feedback '), 'client__collect_feedback');
	assert.equal(normalizeClientToolName('client__collect_feedback'), 'client__collect_feedback');
});

test('validates and normalizes a custom tool draft', () => {
	assert.deepEqual(validateCustomClientToolDraft(VALID_DRAFT), {
		ok: true,
		tool: {
			display_name: 'Collect feedback',
			definition: {
				name: 'client__collect_feedback',
				description: 'Collect structured feedback from the user.',
				read_only: false,
				input_schema: {
					type: 'object',
					properties: { score: { type: 'number' } },
					required: ['score'],
				},
			},
		},
	});
});

test('validates with Draft 2020-12 while preserving a schema declaration', () => {
	const result = validateCustomClientToolDraft({
		...VALID_DRAFT,
		inputSchema: JSON.stringify({
			$schema: 'http://json-schema.org/draft-07/schema#',
			type: 'object',
			properties: {},
		}),
	});

	assert.equal(result.ok, true);
	if (result.ok) {
		assert.deepEqual(result.tool.definition.input_schema, {
			$schema: 'http://json-schema.org/draft-07/schema#',
			type: 'object',
			properties: {},
		});
	}
});

test('rejects malformed schemas and remote references', () => {
	assert.deepEqual(validateCustomClientToolDraft({ ...VALID_DRAFT, inputSchema: '{' }), {
		ok: false,
		error: { field: 'inputSchema', code: 'invalidJson' },
	});
	assert.deepEqual(
		validateCustomClientToolDraft({
			...VALID_DRAFT,
			inputSchema: JSON.stringify({
				type: 'object',
				properties: { value: { $ref: 'https://example.com/schema.json' } },
			}),
		}),
		{ ok: false, error: { field: 'inputSchema', code: 'remoteReference' } },
	);
});

test('rejects schemas that fail Draft 2020-12 meta-validation', () => {
	for (const inputSchema of [
		{
			type: 'object',
			properties: { value: { type: 'str' } },
		},
		{
			type: 'object',
			properties: { value: { minLength: '5' } },
		},
		{
			$schema: 42,
			type: 'object',
			properties: {},
		},
	]) {
		assert.deepEqual(
			validateCustomClientToolDraft({
				...VALID_DRAFT,
				inputSchema: JSON.stringify(inputSchema),
			}),
			{ ok: false, error: { field: 'inputSchema', code: 'invalidSchema' } },
		);
	}
});

test('recovers safely from malformed persisted state', () => {
	assert.deepEqual(parseClientExternalToolStoreState('{'), {
		version: 1,
		customTools: [],
		selections: {},
	});
	assert.deepEqual(
		parseClientExternalToolStoreState(
			JSON.stringify({
				version: 1,
				customTools: [
					{
						display_name: 'Unsafe',
						definition: {
							name: 'client__unsafe',
							description: 'Contains a remote reference.',
							input_schema: {
								type: 'object',
								properties: { value: { $ref: 'https://example.com/schema.json' } },
							},
						},
					},
					{
						display_name: 'Invalid schema',
						definition: {
							name: 'client__invalid_schema',
							description: 'Contains an invalid keyword value.',
							input_schema: {
								type: 'object',
								properties: { value: { minLength: '5' } },
							},
						},
					},
				],
				selections: {},
			}),
		),
		{ version: 1, customTools: [], selections: {} },
	);
});

test('clears a deleted session selection without affecting other sessions', () => {
	setClientExternalToolEnabled('agent-cleanup', 'session-a', 'client__request_user_input', false);
	setClientExternalToolEnabled('agent-cleanup', 'session-b', 'client__request_user_input', false);

	clearClientExternalToolSelection('agent-cleanup', 'session-a');

	assert.deepEqual(getEnabledClientExternalToolNames('agent-cleanup', 'session-a'), [
		'client__request_user_input',
	]);
	assert.deepEqual(getEnabledClientExternalToolNames('agent-cleanup', 'session-b'), []);
});

test('uses built-ins by default and keeps explicit choices per conversation', () => {
	const emptyState = parseClientExternalToolStoreState(null);
	assert.deepEqual(getEnabledClientExternalToolNames('agent-a', 'session-a', emptyState), [
		'client__request_user_input',
	]);

	const selectedState = parseClientExternalToolStoreState(
		JSON.stringify({
			version: 1,
			customTools: [
				{
					display_name: 'Custom',
					definition: {
						name: 'client__custom',
						description: 'Return a custom result.',
						input_schema: { type: 'object', properties: {} },
					},
				},
			],
			selections: {
				'["agent-a","session-a"]': ['client__custom'],
				'["agent-a","session-b"]': [],
			},
		}),
	);
	assert.deepEqual(getEnabledClientExternalToolNames('agent-a', 'session-a', selectedState), [
		'client__custom',
	]);
	assert.deepEqual(getEnabledClientExternalToolNames('agent-a', 'session-b', selectedState), []);
	assert.deepEqual(
		getEnabledClientExternalToolDefinitions('agent-a', 'session-a', selectedState),
		[
			{
				name: 'client__custom',
				description: 'Return a custom result.',
				read_only: false,
				input_schema: { type: 'object', properties: {} },
			},
		],
	);
});
