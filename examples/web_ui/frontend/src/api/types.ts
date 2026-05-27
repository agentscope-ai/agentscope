// ─── Shared ───────────────────────────────────────────────────────────────────

export interface RecordBase {
	id: string;
	created_at: string;
	updated_at: string;
}

export interface ChatModelConfig {
	type: string;
	credential_id: string;
	model: string;
	parameters: Record<string, unknown>;
}

export interface ContextConfig {
	trigger_ratio?: number;
	reserve_ratio?: number;
	tool_result_limit?: number;
	compression_prompt?: string;
	summary_template?: string;
}

export interface ReActConfig {
	max_iters?: number;
	stop_on_reject?: boolean;
}

// ─── Agent ────────────────────────────────────────────────────────────────────

export interface AgentData {
	id: string;
	name: string;
	system_prompt: string;
	context_config: ContextConfig;
	react_config: ReActConfig;
}

export interface AgentRecord extends RecordBase {
	user_id: string;
	data: AgentData;
}

export interface CreateAgentRequest {
	name: string;
	system_prompt?: string;
	context_config?: ContextConfig;
	react_config?: ReActConfig;
}

export interface CreateAgentResponse {
	agent_id: string;
}

export interface UpdateAgentRequest {
	name?: string;
	system_prompt?: string;
	context_config?: ContextConfig;
	react_config?: ReActConfig;
}

export interface AgentListResponse {
	agents: AgentRecord[];
	total: number;
}

/**
 * JSON Schema fragments returned by `GET /agent/schema`. Each fragment is a
 * self-contained JSON Schema object (no `$ref`s across fragments) covering
 * one section of the agent create / edit form.
 */
export interface AgentSchemaResponse {
	identity: JSONSchema;
	context_config: JSONSchema;
	react_config: JSONSchema;
}

// ─── Session ──────────────────────────────────────────────────────────────────

export type SessionSource = 'user' | 'schedule';

export interface SessionConfig {
	name: string;
	chat_model_config: ChatModelConfig;
	/** Fallback model used when the primary model fails. */
	fallback_chat_model_config: ChatModelConfig | null;
	workspace_id: string;
}

// TODO: update when Python side is finalised
export type AgentState = Record<string, unknown>;

export interface SessionRecord extends RecordBase {
	user_id: string;
	agent_id: string;
	source: SessionSource;
	source_schedule_id: string | null;
	config: SessionConfig;
	state: AgentState;
}

export interface CreateSessionRequest {
	agent_id: string;
	workspace_id?: string;
	chat_model_config?: ChatModelConfig | null;
	/** Optional fallback model. Omit (or pass null) for no fallback. */
	fallback_chat_model_config?: ChatModelConfig | null;
}

export interface CreateSessionResponse {
	session_id: string;
}

export interface UpdateSessionRequest {
	name?: string;
	chat_model_config?: ChatModelConfig;
	/**
	 * New fallback model. PATCH semantics:
	 *   - omit the field → leave unchanged
	 *   - set to `null`  → clear the existing fallback
	 *   - set to a value → replace the existing fallback
	 */
	fallback_chat_model_config?: ChatModelConfig | null;
	permission_mode?: PermissionMode;
}

export interface SessionListResponse {
	sessions: SessionRecord[];
	total: number;
}

// ─── JSON Schema ──────────────────────────────────────────────────────────────

/**
 * Subset of JSON Schema property fields the frontend renders. Sourced from
 * Pydantic's `model_json_schema()` output, including the `format: textarea`
 * hint we add via `json_schema_extra` for multi-line strings.
 */
export interface JSONSchemaProperty {
	type?: string;
	format?: string;
	description?: string;
	default?: unknown;
	const?: unknown;
	anyOf?: Array<{ type: string }>;
	title?: string;
	writeOnly?: boolean;
	minimum?: number;
	maximum?: number;
	exclusiveMinimum?: number;
	exclusiveMaximum?: number;
}

export interface JSONSchema {
	title?: string;
	type?: string;
	properties: Record<string, JSONSchemaProperty>;
	required?: string[];
}

// ─── Credential ───────────────────────────────────────────────────────────────

export type CredentialSchemaProperty = JSONSchemaProperty;

// Credential schemas always include title + type (Pydantic always emits them
// for credential data classes); we narrow the generic JSONSchema here so call
// sites that read `schema.title` don't have to do null-checks.
export interface CredentialSchema extends JSONSchema {
	title: string;
	type: string;
}

export interface CredentialSchemasResponse {
	schemas: CredentialSchema[];
}

export interface CredentialRecord extends RecordBase {
	user_id: string;
	data: Record<string, unknown>;
}

export interface CreateCredentialRequest {
	data: Record<string, unknown>;
}

export interface CreateCredentialResponse {
	credential_id: string;
}

export interface UpdateCredentialRequest {
	data: Record<string, unknown>;
}

export interface CredentialListResponse {
	credentials: CredentialRecord[];
	total: number;
}

// ─── Chat ─────────────────────────────────────────────────────────────────────

export type { Msg, ContentBlock } from '@agentscope-ai/agentscope/message';
export type { AgentEvent } from '@agentscope-ai/agentscope/event';
import type {
	UserConfirmResultEvent,
	ExternalExecutionResultEvent,
} from '@agentscope-ai/agentscope/event';
import type { Msg } from '@agentscope-ai/agentscope/message';

export interface ChatRequest {
	agent_id: string;
	session_id: string;
	input: Msg | Msg[] | UserConfirmResultEvent | ExternalExecutionResultEvent | null;
}

// ─── MCP ──────────────────────────────────────────────────────────────────────

export interface StdioMCPConfig {
	type: 'stdio_mcp';
	command: string;
	args?: string[] | null;
	env?: Record<string, string> | null;
	cwd?: string | null;
	encoding_error_handler?: 'strict' | 'ignore' | 'replace';
}

export interface HttpMCPConfig {
	type: 'http_mcp';
	url: string;
	headers?: Record<string, string> | null;
	timeout?: number | null;
}

export interface MCPClient {
	name: string;
	is_stateful: boolean;
	mcp_config: StdioMCPConfig | HttpMCPConfig;
}

export interface ToolInfo {
	name: string;
	description?: string | null;
}

export interface MCPClientStatus extends MCPClient {
	is_healthy: boolean;
	tools: ToolInfo[];
}

// ─── Skill ────────────────────────────────────────────────────────────────────

export interface Skill {
	name: string;
	description: string;
	dir: string;
	markdown: string;
	updated_at: number;
}

export interface AddSkillRequest {
	skill_path: string;
}

// ─── Schedule ─────────────────────────────────────────────────────────────────

export type PermissionMode =
	| 'default'
	| 'accept_edits'
	| 'explore'
	| 'bypass'
	| 'dont_ask'
	| (string & {});

export type ScheduleSource = 'USER' | 'AGENT';

export interface ScheduleData {
	name: string;
	description: string;
	enabled: boolean;
	timezone: string;
	cron_expression: string;
	started_at: string;
	ended_at: string | null;
	chat_model_config: ChatModelConfig;
	stateful: boolean;
	permission_mode: PermissionMode;
	source: ScheduleSource;
	source_session_id: string;
}

export interface ScheduleRecord extends RecordBase {
	user_id: string;
	agent_id: string;
	data: ScheduleData;
}

export interface CreateScheduleRequest {
	name: string;
	description?: string;
	cron_expression: string;
	timezone?: string;
	agent_id: string;
	chat_model_config: ChatModelConfig;
	enabled?: boolean;
	stateful?: boolean;
	permission_mode?: PermissionMode;
}

export interface CreateScheduleResponse {
	schedule_id: string;
}

export interface UpdateScheduleRequest {
	name?: string;
	description?: string;
	cron_expression?: string;
	timezone?: string;
	enabled?: boolean;
	stateful?: boolean;
	permission_mode?: PermissionMode;
}

export interface ScheduleListResponse {
	schedules: ScheduleRecord[];
	total: number;
}

// ─── Model ────────────────────────────────────────────────────────────────────

export interface ModelCard {
	type: 'chat_model';
	name: string;
	label: string;
	status: 'active' | 'deprecated' | 'sunset';
	deprecated_at: string | null;
	input_types: string[];
	output_types: string[];
	context_size: number;
	output_size: number;
	parameter_schema: Record<string, unknown>;
	parameters_overrides: Record<string, Record<string, unknown>>;
}

export interface ListModelRequest {
	provider: string;
}

export interface ListModelResponse {
	models: ModelCard[];
	total: number;
}
