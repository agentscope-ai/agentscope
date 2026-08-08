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

export interface TTSModelConfig {
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

export interface InviteConfig {
	invitable?: boolean;
	invite_description?: string | null;
}

// ─── Agent ────────────────────────────────────────────────────────────────────

export interface AgentData {
	id: string;
	name: string;
	system_prompt: string;
	context_config: ContextConfig;
	react_config: ReActConfig;
	invite_config: InviteConfig;
}

export interface AgentView extends RecordBase {
	user_id: string;
	data: AgentData;
	/**
	 * Whether the current viewer may PATCH/DELETE this agent. `false`
	 * for agents shared to the viewer with read-only permission.
	 */
	editable: boolean;
}

export interface CreateAgentRequest {
	name: string;
	system_prompt?: string;
	context_config?: ContextConfig;
	react_config?: ReActConfig;
	invite_config?: InviteConfig;
}

export interface CreateAgentResponse {
	agent_id: string;
}

export interface UpdateAgentRequest {
	name?: string;
	system_prompt?: string;
	context_config?: ContextConfig;
	react_config?: ReActConfig;
	invite_config?: InviteConfig;
}

export interface AgentListResponse {
	agents: AgentView[];
	total: number;
}

/**
 * @deprecated Superseded by {@link AgentSchemaV2Response}. Kept only for
 * legacy consumers still calling `GET /agent/schema`. The new form flow
 * uses `GET /agent/schema/v2`, which returns the full `AgentData` JSON
 * Schema in a single `schema` field.
 */
export interface AgentSchemaResponse {
	identity: JSONSchema;
	context_config: JSONSchema;
	react_config: JSONSchema;
}

/**
 * Response of `GET /agent/schema/v2`. `schema` is the full `AgentData`
 * JSON Schema (with `$ref`s inlined, `id` filtered out, and
 * `context_config.summary_schema` filtered out). The frontend derives
 * its section grouping directly from `schema.properties`:
 *   - top-level scalar/textarea/boolean properties → "identity" section
 *   - top-level `object`-typed properties (currently `context_config`,
 *     `react_config`, and `invite_config`) → one section each
 */
export interface AgentSchemaV2Response {
	schema: JSONSchema;
}

// ─── Session ──────────────────────────────────────────────────────────────────

export type SessionSource = 'user' | 'schedule' | 'channel';

export interface SessionConfig {
	name: string;
	chat_model_config: ChatModelConfig;
	/** Fallback model used when the primary model fails. */
	fallback_chat_model_config: ChatModelConfig | null;
	/** TTS model configuration. null means TTS is not enabled. */
	tts_model_config: TTSModelConfig | null;
	/** Knowledge bases attached to this session + KB middleware parameters. */
	knowledge_config: SessionKnowledgeConfig | null;
	workspace_id: string;
	/**
	 * Directory the session is focused on — absolute, or relative to the
	 * workspace root, and not confined to it. `null` means the root.
	 * Purely a viewing anchor; it does not move where tools execute.
	 */
	cwd: string | null;
}

// TODO: update when Python side is finalised
export type AgentState = Record<string, unknown>;

export interface SessionRecord extends RecordBase {
	user_id: string;
	agent_id: string;
	source: SessionSource;
	source_schedule_id: string | null;
	source_channel_id: string | null;
	/**
	 * The team this session participates in, if any. Set when the
	 * session is the leader of a team (the session that called
	 * `TeamCreate`) or a worker spawned by `AgentCreate`. `null` for
	 * regular standalone sessions.
	 */
	team_id: string | null;
	config: SessionConfig;
	state: AgentState;
}

export interface CreateSessionRequest {
	agent_id: string;
	workspace_id?: string;
	chat_model_config?: ChatModelConfig | null;
	/** Optional fallback model. Omit (or pass null) for no fallback. */
	fallback_chat_model_config?: ChatModelConfig | null;
	/** Optional TTS model. Omit (or pass null) for no TTS. */
	tts_model_config?: TTSModelConfig | null;
	/** Optional knowledge base attachment. Omit (or null) for none. */
	knowledge_config?: SessionKnowledgeConfig | null;
}

export interface CreateSessionResponse {
	session_id: string;
}

export interface InterruptSessionResponse {
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
	/**
	 * New TTS model. PATCH semantics:
	 *   - omit the field → leave unchanged
	 *   - set to `null`  → disable TTS
	 *   - set to a value → replace the existing TTS config
	 */
	tts_model_config?: TTSModelConfig | null;
	/**
	 * New knowledge base attachment. PATCH semantics:
	 *   - omit the field → leave unchanged
	 *   - set to `null`  → detach every knowledge base
	 *   - set to a value → replace the existing attachment
	 */
	knowledge_config?: SessionKnowledgeConfig | null;
	permission_mode?: PermissionMode;
	/**
	 * New working directory — absolute or relative to the workspace
	 * root, and not confined to it. PATCH semantics:
	 *   - omit the field → leave unchanged
	 *   - set to `null`  → reset to the workspace root
	 *   - set to a value → focus that directory
	 */
	cwd?: string | null;
}

export interface SessionListResponse {
	sessions: SessionView[];
	total: number;
}

/**
 * Response body for `GET /schedule/{id}/sessions`. Returns plain
 * `SessionRecord[]` (no team / is_running enrichment) because
 * scheduled-execution sessions are listed for audit purposes only,
 * not for opening in the chat UI.
 */
export interface ScheduleSessionsResponse {
	sessions: SessionRecord[];
	total: number;
}

// ─── Workspace files ──────────────────────────────────────────────────────────

/** One entry in a workspace directory listing. */
export interface DirectoryEntry {
	name: string;
	is_dir: boolean;
	/** Always null for a directory, and for a file the backend could not stat. */
	size_bytes: number | null;
	/** Last modification time as a Unix timestamp. */
	updated_at: number | null;
}

/** One directory level, plus the path it actually resolved to. */
export interface DirectoryListing {
	/**
	 * Absolute path of the directory that was listed. The only way to
	 * learn where a relative request landed — the workspace root is
	 * backend-dependent and unknowable client-side.
	 */
	path: string;
	entries: DirectoryEntry[];
}

/** Git state of one directory. */
export interface GitStatus {
	/** `null` on a detached HEAD. */
	branch: string | null;
	/** Full commit SHA, or `null` when the repository has no commits. */
	head: string | null;
	/**
	 * Commits ahead of the upstream. `null` means no upstream is
	 * configured, which is a different state from being level with one.
	 */
	ahead: number | null;
	behind: number | null;
	/**
	 * Lines changed relative to HEAD. Untracked files contribute
	 * nothing — `git diff` does not see them — so a session that only
	 * created files reports zero here and a non-zero `untracked`.
	 */
	insertions: number;
	deletions: number;
	/** File counts. */
	staged: number;
	unstaged: number;
	untracked: number;
	conflicted: number;
}

/** Where a session is pointed, and the git state of that place. */
export interface WorkspaceStatus {
	/** Absolute path of the workspace root; not derivable client-side. */
	workdir: string;
	/** Absolute path the session is focused on. Equals `workdir` when unset. */
	cwd: string;
	/**
	 * `null` when there is nothing to report — not a repository, git
	 * unavailable, timed out. The badge is hidden either way.
	 */
	git: GitStatus | null;
}

// ─── Team ─────────────────────────────────────────────────────────────────────

export interface TeamData {
	name: string;
	description: string;
	/** Worker agent ids belonging to the team. */
	member_ids: string[];
}

export interface TeamRecord extends RecordBase {
	user_id: string;
	/** The leader session id — the session that called `TeamCreate`. */
	session_id: string;
	data: TeamData;
}

/**
 * One member entry inside `TeamDetailResponse.members`. Pairs the
 * worker's `AgentView` with its single `session_id` so the UI can
 * navigate straight to the worker's chat.
 */
export interface TeamMemberInfo {
	agent: AgentView;
	/** `null` if the agent is in an inconsistent state (no session). */
	session_id: string | null;
}

/**
 * Resolved team detail returned inline inside `SessionView.team`.
 *
 * The leader's `AgentView` is looked up from the team's
 * `session_id` → `session.agent_id` chain on the server side.
 */
export interface TeamDetailResponse {
	team: TeamRecord;
	leader_agent: AgentView | null;
	members: TeamMemberInfo[];
}

/**
 * A session's unified status. `running` means a worker somewhere holds
 * its run lease; the `awaiting_*` values mean nobody is running it but
 * its stored context is parked on a pending tool call.
 */
export type SessionStatus = 'running' | 'idle' | 'awaiting_permission' | 'awaiting_external_result';

/**
 * Per-session bundle returned by `GET /sessions/?agent_id=...`.
 *
 * Bundles what the chat UI needs to render a session without follow-up
 * requests: the persisted record, its status, and — when the session
 * participates in a team — the resolved team detail.
 */
export interface SessionView {
	/**
	 * The record with the bulk of `state` stripped: `context`, `summary`
	 * and `tool_context` arrive cleared, since they hold the model's
	 * conversation and every file it has read. `permission_context` and
	 * `tasks_context` survive — the panels seed from them. Messages come
	 * from `GET /sessions/{id}/messages`.
	 */
	session: SessionRecord;
	/**
	 * @deprecated Use {@link status}. True only while a worker holds the
	 * run lease, which a session parked on a confirmation prompt does
	 * not — so this reads `false` for a session visibly waiting on you.
	 */
	is_running: boolean;
	/** Exactly one applies at a time, so one indicator renders it. */
	status: SessionStatus;
	team: TeamDetailResponse | null;
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
	enum?: unknown[];
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

export interface CredentialView extends RecordBase {
	user_id: string;
	/**
	 * Credential payload. When the current viewer is not the owner
	 * (shared credential), only `type` and `name` are populated —
	 * secret fields are stripped server-side.
	 */
	data: Record<string, unknown>;
	/**
	 * Whether the current viewer may PATCH/DELETE this credential.
	 * `false` for credentials shared with read-only permission.
	 */
	editable: boolean;
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
	credentials: CredentialView[];
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
	/**
	 * Why listing this MCP's tools failed. `null` when healthy — a red dot
	 * alone leaves nothing to act on, since a wrong API key, an unreachable
	 * host and a missing command all look the same.
	 */
	error: string | null;
}

// ─── Skill ────────────────────────────────────────────────────────────────────

export interface Skill {
	name: string;
	description: string;
	dir: string;
	markdown: string;
	updated_at: number;
}

/**
 * @deprecated The path is resolved on the server, which only means
 * anything for a single-host deployment. Upload a folder or install
 * from the library instead.
 */
export interface AddSkillRequest {
	skill_path: string;
}

// ─── Hub ──────────────────────────────────────────────────────────────────────

/** One registered hub, as shown in the hub picker. */
export interface HubInfo {
	hub_id: string;
	display_name: string;
	description: string;
	/** `null` when the hub has no icon; fall back rather than leave a gap. */
	icon_url: string | null;
}

/**
 * Query for browsing one hub. Pagination is cursor-based — pass the previous
 * page's `next_cursor` to load more; a `null` cursor means the end.
 */
export interface HubBrowseParams {
	/** Keyword search. Some hubs answer this from a separate, unpaginated endpoint. */
	q?: string;
	cursor?: string;
	/** 1-200, defaults to 20 server-side. */
	limit?: number;
}

interface HubCardBase {
	/** The hub this card came from. Together with `id` it addresses the card globally. */
	hub_id: string;
	/**
	 * The card's id on its hub — opaque, and not necessarily URL-safe (some
	 * registries use ids containing `:`). Always encode it into a path.
	 */
	id: string;
	name: string;
	display_name?: string | null;
	description: string;
	tags: string[];
	version?: string | null;
}

/**
 * An MCP listing: a *template*, not something connectable. `config_template`
 * holds `${...}` placeholders that the server fills from the values submitted
 * at install time — never substitute them client-side.
 */
export interface MCPCard extends HubCardBase {
	is_stateful: boolean;
	updated_at?: number | null;
	/** Who published it. `null` when the hub does not say. */
	author?: string | null;
	/** An image representing it. `null` when the hub offers none. */
	icon_url?: string | null;
	/** Its page on the hub's website. `null` if it has none. */
	url?: string | null;
	/** `null` means uncounted, which is not the same as zero. */
	installs?: number | null;
	downloads?: number | null;
	/** `none` — install directly, no form. `inputs` — render `inputs_schema`. */
	auth: 'none' | 'inputs';
	/**
	 * JSON Schema for the install form. Empty (no `properties`) when the card
	 * needs no configuration, so branch on `auth` before rendering.
	 * Secret fields carry `writeOnly: true` / `format: 'password'`.
	 */
	inputs_schema: Partial<JSONSchema>;
	/**
	 * The server's long-form docs. Only populated by the detail endpoint —
	 * READMEs run to tens of kilobytes, so listings leave them out.
	 */
	readme?: string | null;
	/** Shown read-only; the placeholders are resolved server-side. */
	config_template: StdioMCPConfig | HttpMCPConfig;
}

/** A skill listing. Unlike an MCP there is nothing to configure. */
export interface SkillCard extends HubCardBase {
	updated_at?: number | null;
	/** Who published it. `null` when the hub does not say. */
	author?: string | null;
	/**
	 * An image representing the skill. `null` when the hub offers none —
	 * fall back rather than rendering a broken image.
	 */
	icon_url?: string | null;
	/**
	 * How many times the skill has been installed. `null` means the hub does
	 * not count installs — which is not the same as zero, so don't render it.
	 */
	installs?: number | null;
	/** How many times it has been downloaded. `null` when uncounted. */
	downloads?: number | null;
	/** The skill's page on the hub's website. `null` if it has none. */
	url?: string | null;
	/** The `SKILL.md` body — only populated by the detail endpoint. */
	markdown?: string | null;
	metadata: Record<string, unknown>;
}

export interface MCPHubPage {
	cards: MCPCard[];
	/** `null` when this is the last page. */
	next_cursor: string | null;
}

export interface SkillHubPage {
	cards: SkillCard[];
	next_cursor: string | null;
}

/**
 * The outcome of putting library MCPs into a workspace, reported per MCP:
 * connecting happens one at a time, so a bad API key on the third pick must
 * not throw away the two that worked.
 */
export interface AddFromLibraryResponse {
	/** Now in the workspace. Excludes ones already present. */
	added: string[];
	/** Whatever could not be added, mapped to why. */
	failed: Record<string, string>;
}

/** A library edit. Omitted fields are left alone. */
export interface UpdateMCPRequest {
	name?: string;
	/**
	 * New answers, merged over the stored ones — send only what changed, so
	 * a write-only field the form never echoed back survives.
	 */
	values?: Record<string, unknown>;
	enabled?: boolean;
}

export interface InstallMCPRequest {
	/**
	 * Name to install under, defaulting to the card's. Must match
	 * `[a-zA-Z0-9_-]+`; use it to resolve a 409 name clash.
	 */
	name?: string | null;
	/** Answers to `inputs_schema`, e.g. API keys. */
	values: Record<string, unknown>;
}

// ─── Installed MCPs and skills ────────────────────────────────────────────────

/**
 * One MCP in the user's own library, which is where an install lands —
 * distinct from `WorkspaceMCP`, which is what one session's workspace holds.
 *
 * The rendered config is not exposed: it carries the values submitted at
 * install time, API keys included.
 */
export interface MCPView {
	id: string;
	/** Unique per user — the handle a workspace refers to it by. */
	name: string;
	is_stateful: boolean;
	enabled: boolean;
	/**
	 * Snapshotted from the card at install time, so they survive the hub
	 * going away — and may lag behind it.
	 */
	display_name: string | null;
	description: string;
	tags: string[];
	author: string | null;
	icon_url: string | null;
	url: string | null;
	/** `null` when the MCP was added by hand rather than from a hub. */
	hub_id: string | null;
	card_id: string | null;
	version: string | null;
}

/**
 * One skill in the user's own library. Unlike an MCP, the skill's files are
 * not stored — the record says where they came from, and the archive is
 * re-fetched from the hub when the skill reaches a workspace.
 */
export interface SkillView {
	id: string;
	/** Unique per user — the handle a workspace refers to it by. */
	name: string;
	enabled: boolean;
	display_name: string | null;
	description: string;
	tags: string[];
	/** Snapshotted from the card at install time, so the library keeps the
	 *  identity of the listing it came from. */
	author: string | null;
	icon_url: string | null;
	url: string | null;
	hub_id: string | null;
	card_id: string | null;
	version: string | null;
}

/** A library skill with its `SKILL.md` body, from the detail endpoint. */
export interface SkillRecord extends SkillView {
	markdown: string;
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

// ─── Embedding ────────────────────────────────────────────────────────────────

export interface EmbeddingModelConfig {
	type: string;
	credential_id: string;
	model: string;
	/**
	 * Output vector dimensions, pinned at config time. Required because
	 * the backend uses it to size the vector store collection and to
	 * validate against the manager's `DimensionPolicy`.
	 */
	dimensions: number;
	parameters: Record<string, unknown>;
}

export interface EmbeddingModelCard {
	type: 'embedding_model';
	name: string;
	label: string;
	status: 'active' | 'deprecated' | 'sunset';
	input_types: string[];
	output_types: string[];
	context_size: number | null;
	/** Default output dimensions for this model. */
	dimensions: number;
	/**
	 * If set, the only dimensions this model can produce (Matryoshka).
	 * `null` means the model is fixed-dim at `dimensions`.
	 */
	supported_dimensions: number[] | null;
	parameter_schema: Record<string, unknown>;
	parameter_overrides: Record<string, Record<string, unknown>>;
}

/** Response of `GET /embedding-model/` — the provider's full catalogue. */
export interface ListEmbeddingModelResponse {
	models: EmbeddingModelCard[];
	total: number;
}

// ─── Knowledge Base ───────────────────────────────────────────────────────────

/**
 * Knowledge base view as exposed by the API. Mirrors
 * :class:`agentscope.app._service.KnowledgeBaseView`.
 */
export interface KnowledgeBaseView {
	id: string;
	name: string;
	description: string;
	embedding_model_config: EmbeddingModelConfig;
	created_at: string;
	updated_at: string;
	/**
	 * Whether the current viewer may modify this knowledge base (edit
	 * metadata, add/delete documents). `false` for knowledge bases
	 * shared with read-only permission.
	 */
	editable: boolean;
}

export interface ListKnowledgeBasesResponse {
	knowledge_bases: KnowledgeBaseView[];
	total: number;
}

export interface CreateKnowledgeBaseRequest {
	name: string;
	description?: string;
	embedding_model_config: EmbeddingModelConfig;
}

export interface CreateKnowledgeBaseResponse {
	knowledge_base_id: string;
}

/**
 * Body for `PATCH /knowledge_bases/{id}`. Only mutable fields can be
 * sent; the embedding model is pinned at creation time and cannot
 * change because the underlying collection is sized to its dimension.
 */
export interface UpdateKnowledgeBaseRequest {
	name?: string;
	description?: string;
}

/**
 * Lifecycle states a document can be in. Mirrors
 * :class:`agentscope.app.storage.KnowledgeDocumentStatus`.
 *
 * - `pending` — accepted, blob stored, indexing not yet started.
 * - `parsing` / `chunking` / `indexing` — worker phases.
 * - `ready` — chunks committed to the vector store.
 * - `error` — terminal failure; `error` field carries the reason.
 */
export type KnowledgeDocumentStatus =
	| 'pending'
	| 'parsing'
	| 'chunking'
	| 'indexing'
	| 'ready'
	| 'error';

/**
 * Document view returned by `/knowledge_bases/{id}/documents` and
 * `/knowledge_bases/{id}/documents/status`. Mirrors
 * :class:`agentscope.app._router._schema.KnowledgeDocumentView`.
 */
export interface KnowledgeDocumentView {
	id: string;
	filename: string;
	size: number;
	content_type: string | null;
	status: KnowledgeDocumentStatus;
	error: string | null;
	chunk_count: number;
	created_at: string;
	updated_at: string;
}

export interface ListKnowledgeDocumentsResponse {
	documents: KnowledgeDocumentView[];
	total: number;
}

export interface ListKnowledgeDocumentStatusResponse {
	items: KnowledgeDocumentView[];
}

export interface UploadKnowledgeDocumentResponse {
	document_id: string;
	filename: string;
	status: KnowledgeDocumentStatus;
}

export interface SearchKnowledgeBaseRequest {
	query: string;
	top_k?: number;
}

/**
 * Lightweight chunk shape returned inside `VectorSearchResult`. Mirrors
 * :class:`agentscope.rag.Chunk` — content is the raw `TextBlock` /
 * `DataBlock` discriminated union the backend ships.
 */
export interface KnowledgeChunk {
	content: { type: 'text'; text: string; id?: string } | { type: string; [key: string]: unknown };
	source: string;
	chunk_index: number;
	total_chunks: number;
	metadata: Record<string, unknown>;
}

/**
 * One vector search hit returned by the knowledge base search endpoint.
 * Mirrors :class:`agentscope.rag.VectorSearchResult` on the backend.
 */
export interface VectorSearchResult {
	score: number;
	document_id: string;
	chunk: KnowledgeChunk;
}

export interface SearchKnowledgeBaseResponse {
	results: VectorSearchResult[];
	total: number;
}

/**
 * Mirrors :class:`agentscope.app.rag.knowledge_base_manager.DimensionPolicyKind`.
 */
export type DimensionPolicyKind = 'any' | 'fixed' | 'locked_by_existing';

/**
 * Mirrors :class:`agentscope.app.rag.knowledge_base_manager.DimensionPolicy`.
 */
export interface DimensionPolicy {
	kind: DimensionPolicyKind;
	dimension: number | null;
}

/** One credential and the embedding models it can serve, post-policy. */
export interface KbEmbeddingProvider {
	credential: CredentialView;
	models: EmbeddingModelCard[];
}

/**
 * Response of `GET /knowledge_bases/embedding_models`.
 *
 * Server-side already filtered models by the manager's
 * :class:`DimensionPolicy` and narrowed matryoshka cards to the
 * locked dimension when applicable. The policy is included so the
 * UI can render an explanatory banner.
 */
export interface ListKbEmbeddingModelsResponse {
	providers: KbEmbeddingProvider[];
	policy: DimensionPolicy;
}

/**
 * Session-level knowledge base attachment. Persisted on
 * :class:`SessionConfig.knowledge_config` and translated into a
 * `KnowledgeBaseMiddleware` at chat-run time.
 *
 * `parameters` holds the user-tunable middleware fields verbatim — its
 * accepted keys/values are described by the JSON Schema returned from
 * `GET /knowledge_bases/middleware/parameters_schema`.
 */
export interface SessionKnowledgeConfig {
	knowledge_base_ids: string[];
	parameters: Record<string, unknown>;
}

/** Response of `GET /knowledge_bases/middleware/parameters_schema`. */
export interface KbMiddlewareParametersSchemaResponse {
	parameter_schema: Record<string, unknown>;
}

/** Response of `GET /knowledge_bases/supported_content_types`. */
export interface ListSupportedContentTypesResponse {
	/** Union of IANA media types every registered parser handles. */
	media_types: string[];
	/** Union of filename extensions (each starting with `.`). */
	extensions: string[];
}

// ─── Channel ──────────────────────────────────────────────────────────────────

// How inbound messages are grouped into agent sessions.
export type SessionScope = 'per_chat' | 'per_chat_user';

// One routing rule: match an inbound event, then pick the agent and how
// its session is grouped. Rules are ordered; the first match wins and the
// last must be a catch-all (match_value === '*').
export interface ChannelBinding {
	match_key: string;
	match_value: string;
	agent_id: string;
	session_scope: SessionScope;
}

export interface RoutingConfig {
	bindings: ChannelBinding[];
}

export interface SessionSettings {
	chat_model_config: ChatModelConfig;
	fallback_chat_model_config?: ChatModelConfig | null;
	permission_mode: PermissionMode;
}

export interface ChannelRecord {
	id: string;
	channel_type: string;
	name: string | null;
	user_id: string;
	platform_bot_id: string;
	enabled: boolean;
	platform_config: Record<string, unknown>;
	routing: RoutingConfig;
	session: SessionSettings;
	created_at: string;
	updated_at: string;
}

export interface CreateChannelRequest {
	channel_type: string;
	name?: string | null;
	credentials: Record<string, unknown>;
	platform_config?: Record<string, unknown>;
	routing: RoutingConfig;
	session: SessionSettings;
	enabled?: boolean;
}

export interface UpdateChannelRequest {
	name?: string | null;
	platform_config?: Record<string, unknown>;
	routing?: RoutingConfig;
	session?: SessionSettings;
	enabled?: boolean;
}

export interface ChannelTypeSchema {
	channel_type: string;
	display_name: string;
	description?: string;
	icon_url?: string;
	credentials_schema: Record<string, unknown>;
	config_schema: Record<string, unknown>;
	platform_bot_id_field?: string;
}

export type ChannelState = 'stopped' | 'connecting' | 'retrying' | 'connected' | 'failed';

export interface ChannelStatus {
	state: ChannelState;
	last_error: string;
}

export interface ChannelSessionsResponse {
	sessions: SessionRecord[];
	total: number;
}

export interface ChannelChatIdsResponse {
	chats: { chat_id: string; name: string; source: string }[];
}

// ─── TTS ──────────────────────────────────────────────────────────────────────

export interface TTSModelCard {
	type: 'tts_model';
	name: string;
	label: string;
	status: 'active' | 'deprecated' | 'sunset';
	deprecated_at: string | null;
	input_types: string[];
	output_types: string[];
	realtime: boolean;
	parameter_schema: Record<string, unknown>;
	parameters_overrides: Record<string, Record<string, unknown>>;
}

export interface ListTTSModelResponse {
	models: TTSModelCard[];
	total: number;
}

// ─── Health ───────────────────────────────────────────────────────────────────

/** `disabled` means the deployment turned an optional feature off, not that it is down. */
export type ComponentStatus = 'ok' | 'not_ready' | 'disabled';

export interface HealthResponse {
	status: 'ok' | 'not_ready';
	version: string;
	components: Record<string, ComponentStatus>;
}
