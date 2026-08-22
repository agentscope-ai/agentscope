import Ajv2020 from 'ajv/dist/2020.js';

import { CLIENT_EXTERNAL_TOOL_DEFINITIONS } from './client-external-tool-definitions.ts';
import type { ClientExternalToolDefinition } from '../api/types.ts';

export const CLIENT_EXTERNAL_TOOL_STORAGE_KEY = 'agentscope.client-external-tools.v1';

const CLIENT_TOOL_PREFIX = 'client__';
const MAX_TOOL_NAME_LENGTH = 64;
const MAX_DISPLAY_NAME_LENGTH = 80;
const MAX_DESCRIPTION_LENGTH = 2000;
const MAX_SCHEMA_BYTES = 64 * 1024;
const MAX_CLIENT_TOOLS = 16;
const TOOL_NAME_PATTERN = /^client__[a-zA-Z0-9_-]+$/;
const REFERENCE_KEYWORDS = new Set(['$ref', '$dynamicRef', '$recursiveRef']);

export interface CustomClientExternalTool {
	display_name: string;
	definition: ClientExternalToolDefinition;
}

export interface CustomClientExternalToolDraft {
	displayName: string;
	name: string;
	description: string;
	inputSchema: string;
}

export type CustomToolValidationField = 'displayName' | 'name' | 'description' | 'inputSchema';

export type CustomToolValidationCode =
	| 'required'
	| 'displayNameTooLong'
	| 'invalidName'
	| 'duplicateName'
	| 'descriptionTooLong'
	| 'invalidJson'
	| 'invalidSchema'
	| 'schemaMustBeObject'
	| 'schemaPropertiesRequired'
	| 'remoteReference'
	| 'schemaTooLarge'
	| 'toolLimit';

export type CustomToolValidationError = {
	field: CustomToolValidationField;
	code: CustomToolValidationCode;
};

export type CustomToolValidationResult =
	| { ok: true; tool: CustomClientExternalTool }
	| { ok: false; error: CustomToolValidationError };

export interface ClientExternalToolStoreState {
	version: 1;
	customTools: CustomClientExternalTool[];
	selections: Record<string, string[]>;
}

const EMPTY_STATE: ClientExternalToolStoreState = {
	version: 1,
	customTools: [],
	selections: {},
};

const builtInNames = new Set(CLIENT_EXTERNAL_TOOL_DEFINITIONS.map((tool) => tool.name));
const listeners = new Set<() => void>();
let storageListenerAttached = false;
const schemaValidator = new Ajv2020({ strict: false });

function isRecord(value: unknown): value is Record<string, unknown> {
	return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function isDefinition(value: unknown): value is ClientExternalToolDefinition {
	return (
		isRecord(value) &&
		typeof value.name === 'string' &&
		typeof value.description === 'string' &&
		(value.read_only === undefined || typeof value.read_only === 'boolean') &&
		isRecord(value.input_schema)
	);
}

function isCustomTool(value: unknown): value is CustomClientExternalTool {
	return (
		isRecord(value) && typeof value.display_name === 'string' && isDefinition(value.definition)
	);
}

function sanitizeCustomTools(value: unknown): CustomClientExternalTool[] {
	if (!Array.isArray(value)) return [];
	const tools: CustomClientExternalTool[] = [];
	const occupiedNames = new Set(builtInNames);
	for (const candidate of value) {
		if (!isCustomTool(candidate) || tools.length + builtInNames.size >= MAX_CLIENT_TOOLS) {
			continue;
		}
		const validated = validateCustomClientToolDraft(
			{
				displayName: candidate.display_name,
				name: candidate.definition.name,
				description: candidate.definition.description,
				inputSchema: JSON.stringify(candidate.definition.input_schema),
			},
			occupiedNames,
		);
		if (!validated.ok || validated.tool.definition.name !== candidate.definition.name) continue;
		tools.push(validated.tool);
		occupiedNames.add(validated.tool.definition.name);
	}
	return tools;
}

function makeSessionKey(agentId: string, sessionId: string): string {
	return JSON.stringify([agentId, sessionId]);
}

function readStoredState(): ClientExternalToolStoreState {
	if (typeof window === 'undefined') return EMPTY_STATE;

	try {
		return parseClientExternalToolStoreState(
			window.localStorage.getItem(CLIENT_EXTERNAL_TOOL_STORAGE_KEY),
		);
	} catch {
		return EMPTY_STATE;
	}
}

let snapshot = readStoredState();

function emit(nextState: ClientExternalToolStoreState): void {
	snapshot = nextState;
	try {
		window.localStorage.setItem(CLIENT_EXTERNAL_TOOL_STORAGE_KEY, JSON.stringify(nextState));
	} catch {
		// Keep the current tab functional when storage is unavailable or full.
	}
	listeners.forEach((listener) => listener());
}

function attachStorageListener(): void {
	if (storageListenerAttached || typeof window === 'undefined') return;
	storageListenerAttached = true;
	window.addEventListener('storage', (event) => {
		if (event.key !== CLIENT_EXTERNAL_TOOL_STORAGE_KEY) return;
		snapshot = parseClientExternalToolStoreState(event.newValue);
		listeners.forEach((listener) => listener());
	});
}

function containsRemoteReference(value: unknown): boolean {
	if (Array.isArray(value)) return value.some(containsRemoteReference);
	if (!isRecord(value)) return false;

	return Object.entries(value).some(
		([key, child]) =>
			(REFERENCE_KEYWORDS.has(key) && typeof child === 'string' && !child.startsWith('#')) ||
			containsRemoteReference(child),
	);
}

function schemaByteLength(schema: Record<string, unknown>): number {
	return new TextEncoder().encode(JSON.stringify(schema)).byteLength;
}

function isValidDraft202012Schema(schema: Record<string, unknown>): boolean {
	// Match the backend's Draft202012Validator.check_schema behavior instead
	// of letting Ajv switch validators based on a user-provided `$schema` URI.
	if ('$schema' in schema && typeof schema.$schema !== 'string') return false;
	const schemaToValidate = { ...schema };
	delete schemaToValidate.$schema;
	try {
		return schemaValidator.validateSchema(schemaToValidate) === true;
	} catch {
		return false;
	}
}

export function normalizeClientToolName(name: string): string {
	const trimmed = name.trim();
	return trimmed.startsWith(CLIENT_TOOL_PREFIX) ? trimmed : `${CLIENT_TOOL_PREFIX}${trimmed}`;
}

export function parseClientExternalToolStoreState(
	serialized: string | null,
): ClientExternalToolStoreState {
	if (!serialized) return EMPTY_STATE;

	try {
		const value: unknown = JSON.parse(serialized);
		if (!isRecord(value) || value.version !== 1) return EMPTY_STATE;

		const customTools = sanitizeCustomTools(value.customTools);
		const selections: Record<string, string[]> = {};
		if (isRecord(value.selections)) {
			for (const [key, names] of Object.entries(value.selections)) {
				if (Array.isArray(names)) {
					selections[key] = names
						.filter(
							(name, index): name is string =>
								typeof name === 'string' &&
								TOOL_NAME_PATTERN.test(name) &&
								names.indexOf(name) === index,
						)
						.slice(0, MAX_CLIENT_TOOLS);
				}
			}
		}

		return { version: 1, customTools, selections };
	} catch {
		return EMPTY_STATE;
	}
}

export function validateCustomClientToolDraft(
	draft: CustomClientExternalToolDraft,
	occupiedNames: ReadonlySet<string> = builtInNames,
): CustomToolValidationResult {
	const displayName = draft.displayName.trim();
	if (!displayName) return { ok: false, error: { field: 'displayName', code: 'required' } };
	if (displayName.length > MAX_DISPLAY_NAME_LENGTH) {
		return { ok: false, error: { field: 'displayName', code: 'displayNameTooLong' } };
	}

	const name = normalizeClientToolName(draft.name);
	if (name.length > MAX_TOOL_NAME_LENGTH || !TOOL_NAME_PATTERN.test(name)) {
		return { ok: false, error: { field: 'name', code: 'invalidName' } };
	}
	if (occupiedNames.has(name)) {
		return { ok: false, error: { field: 'name', code: 'duplicateName' } };
	}

	const description = draft.description.trim();
	if (!description) return { ok: false, error: { field: 'description', code: 'required' } };
	if (description.length > MAX_DESCRIPTION_LENGTH) {
		return { ok: false, error: { field: 'description', code: 'descriptionTooLong' } };
	}

	let schema: unknown;
	try {
		schema = JSON.parse(draft.inputSchema);
	} catch {
		return { ok: false, error: { field: 'inputSchema', code: 'invalidJson' } };
	}
	if (!isRecord(schema) || schema.type !== 'object') {
		return { ok: false, error: { field: 'inputSchema', code: 'schemaMustBeObject' } };
	}
	if (!isRecord(schema.properties)) {
		return { ok: false, error: { field: 'inputSchema', code: 'schemaPropertiesRequired' } };
	}
	if (!isValidDraft202012Schema(schema)) {
		return { ok: false, error: { field: 'inputSchema', code: 'invalidSchema' } };
	}
	if (containsRemoteReference(schema)) {
		return { ok: false, error: { field: 'inputSchema', code: 'remoteReference' } };
	}
	if (schemaByteLength(schema) > MAX_SCHEMA_BYTES) {
		return { ok: false, error: { field: 'inputSchema', code: 'schemaTooLarge' } };
	}

	return {
		ok: true,
		tool: {
			display_name: displayName,
			definition: { name, description, read_only: false, input_schema: schema },
		},
	};
}

export function subscribeClientExternalToolStore(listener: () => void): () => void {
	attachStorageListener();
	listeners.add(listener);
	return () => listeners.delete(listener);
}

export function getClientExternalToolStoreSnapshot(): ClientExternalToolStoreState {
	return snapshot;
}

export function getClientExternalToolServerSnapshot(): ClientExternalToolStoreState {
	return EMPTY_STATE;
}

export function getEnabledClientExternalToolNames(
	agentId: string | null,
	sessionId: string | null,
	state: ClientExternalToolStoreState = snapshot,
): string[] {
	if (!agentId || !sessionId) return [];
	const stored = state.selections[makeSessionKey(agentId, sessionId)];
	if (!stored) return [...builtInNames];
	const availableNames = new Set([
		...builtInNames,
		...state.customTools.map((tool) => tool.definition.name),
	]);
	return stored.filter((name) => availableNames.has(name));
}

export function getEnabledClientExternalToolDefinitions(
	agentId: string,
	sessionId: string,
	state: ClientExternalToolStoreState = snapshot,
): ClientExternalToolDefinition[] {
	const definitions = [
		...CLIENT_EXTERNAL_TOOL_DEFINITIONS,
		...state.customTools.map((tool) => tool.definition),
	];
	const byName = new Map(definitions.map((definition) => [definition.name, definition]));
	return getEnabledClientExternalToolNames(agentId, sessionId, state)
		.map((name) => byName.get(name))
		.filter((definition): definition is ClientExternalToolDefinition => Boolean(definition));
}

export function setClientExternalToolEnabled(
	agentId: string,
	sessionId: string,
	toolName: string,
	enabled: boolean,
): void {
	const current = new Set(getEnabledClientExternalToolNames(agentId, sessionId));
	if (enabled) current.add(toolName);
	else current.delete(toolName);
	emit({
		...snapshot,
		selections: {
			...snapshot.selections,
			[makeSessionKey(agentId, sessionId)]: [...current],
		},
	});
}

export function clearClientExternalToolSelection(agentId: string, sessionId: string): void {
	const key = makeSessionKey(agentId, sessionId);
	if (!(key in snapshot.selections)) return;
	const selections = { ...snapshot.selections };
	delete selections[key];
	emit({ ...snapshot, selections });
}

export function saveCustomClientExternalTool(
	draft: CustomClientExternalToolDraft,
	originalName: string | null,
	agentId: string | null,
	sessionId: string | null,
): CustomToolValidationResult {
	if (
		!originalName &&
		CLIENT_EXTERNAL_TOOL_DEFINITIONS.length + snapshot.customTools.length >= MAX_CLIENT_TOOLS
	) {
		return { ok: false, error: { field: 'name', code: 'toolLimit' } };
	}

	const occupiedNames = new Set([
		...builtInNames,
		...snapshot.customTools
			.map((tool) => tool.definition.name)
			.filter((name) => name !== originalName),
	]);
	const validated = validateCustomClientToolDraft(draft, occupiedNames);
	if (!validated.ok) return validated;

	const nextName = validated.tool.definition.name;
	const customTools = originalName
		? snapshot.customTools.map((tool) =>
				tool.definition.name === originalName ? validated.tool : tool,
			)
		: [...snapshot.customTools, validated.tool];
	const selections = Object.fromEntries(
		Object.entries(snapshot.selections).map(([key, names]) => [
			key,
			names.map((name) => (name === originalName ? nextName : name)),
		]),
	);
	if (!originalName && agentId && sessionId) {
		const key = makeSessionKey(agentId, sessionId);
		selections[key] = [...getEnabledClientExternalToolNames(agentId, sessionId), nextName];
	}

	emit({ version: 1, customTools, selections });
	return validated;
}

export function deleteCustomClientExternalTool(toolName: string): void {
	emit({
		version: 1,
		customTools: snapshot.customTools.filter((tool) => tool.definition.name !== toolName),
		selections: Object.fromEntries(
			Object.entries(snapshot.selections).map(([key, names]) => [
				key,
				names.filter((name) => name !== toolName),
			]),
		),
	});
}
