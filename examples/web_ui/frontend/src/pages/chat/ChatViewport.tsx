import type { PermissionContext } from '@agentscope-ai/agentscope/permission';
import type { TaskContext } from '@agentscope-ai/agentscope/state';
import {
	BookText,
	ChevronDown,
	Database,
	ListTodo,
	PanelRight,
	ShieldCheck,
	UsersRound,
} from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import type {
	ChatModelConfig,
	PermissionMode,
	SessionKnowledgeConfig,
	TTSModelConfig,
	UpdateSessionRequest,
} from '@/api';
import { sessionApi } from '@/api';
import MCPSvg from '@/assets/images/mcp.svg?react';
import { ChatContent } from '@/components/chat/ChatContent.tsx';
import { SubagentHitlCard } from '@/components/chat/SubagentHitlCard';
import { CreateCredentialDialog } from '@/components/dialog/CreateCredentialDialog';
import { KnowledgeBasePanel } from '@/components/panel/KnowledgeBasePanel';
import { McpPanel } from '@/components/panel/McpPanel';
import { PanelDock, type PanelDescriptor, type PanelKey } from '@/components/panel/PanelDock.tsx';
import { PermissionPanel } from '@/components/panel/PermissionPanel';
import { SkillPanel } from '@/components/panel/SkillPanel';
import { TaskPanel } from '@/components/panel/TaskPanel';
import { TeamPanel } from '@/components/panel/TeamPanel';
import { KnowledgeBaseParametersPopover } from '@/components/popover/KnowledgeBaseParametersPopover';
import { ModelParametersPopover } from '@/components/popover/ModelParametersPopover';
import { LlmSelect } from '@/components/select/LlmSelect';
import { PermissionModeSelect } from '@/components/select/PermissionModeSelect.tsx';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
	DropdownMenu,
	DropdownMenuCheckboxItem,
	DropdownMenuContent,
	DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import {
	ResizableHandle,
	ResizablePanel,
	ResizablePanelGroup,
} from '@/components/ui/resizable.tsx';
import { SidebarTrigger } from '@/components/ui/sidebar';
import { useAvailableModels } from '@/hooks/useAvailableModels';
import { useKnowledgeBaseMiddlewareSchema } from '@/hooks/useKnowledgeBaseMiddlewareSchema';
import { useKnowledgeBases } from '@/hooks/useKnowledgeBases';
import { useMessages } from '@/hooks/useMessages';
import { useSessions } from '@/hooks/useSessions';
import { useWorkspace } from '@/hooks/useWorkspace.ts';
import { useWorkspaceStatus } from '@/hooks/useWorkspaceStatus';
import { useTranslation } from '@/i18n/useI18n';

interface ChatViewportProps {
	/**
	 * The agent that owns the session being viewed. May be the
	 * user-facing leader agent or — when drilled into a team member
	 * via the URL's `:memberId` slot — a worker agent.
	 */
	agentId: string | null;
	/**
	 * The session whose messages, model config, permission mode, and
	 * workspace drive every control rendered here.
	 */
	sessionId: string | null;
	/**
	 * Optional hook invoked when a team membership change arrives on
	 * this viewport's SSE stream. The outer page owns the session list
	 * that backs the team sidebar, so it must be told to refetch too;
	 * passing this callback wires that signal up.
	 */
	onTeamUpdated?: () => void;
}

/** Maximum number of panels stacked in a single dock column. */
const MAX_PANELS_PER_COLUMN = 2;

/** localStorage key holding the dock layout across page navigations. */
const PANEL_LAYOUT_KEY = 'chat_panel_layout';

// Typed as a full Record so adding a PanelKey without listing it here
// is a compile error rather than a silently unrestorable panel.
const KNOWN_PANELS: Record<PanelKey, true> = {
	plan: true,
	mcp: true,
	skill: true,
	permission: true,
	knowledge: true,
	team: true,
};

/**
 * Restore the persisted dock layout, dropping anything that is no
 * longer a known panel (keys get renamed/removed across releases).
 *
 * @returns The stored layout, or an empty one when absent or corrupt.
 */
function loadPanelLayout(): PanelKey[][] {
	try {
		const parsed: unknown = JSON.parse(localStorage.getItem(PANEL_LAYOUT_KEY) ?? '[]');
		if (!Array.isArray(parsed)) return [];
		return parsed
			.map((column: unknown) =>
				Array.isArray(column)
					? column.filter((key): key is PanelKey => key in KNOWN_PANELS)
					: [],
			)
			.filter((column) => column.length > 0);
	} catch {
		return [];
	}
}

/**
 * Insert a panel into the dock layout. Scans columns left to right and
 * appends to the first one with spare room; if every column is full a
 * new rightmost column is created. No-op when the panel is already
 * open.
 *
 * @param layout - The current column/panel arrangement.
 * @param key - The panel to open.
 * @returns A new layout array (the input is never mutated).
 */
function openPanelInLayout(layout: PanelKey[][], key: PanelKey): PanelKey[][] {
	if (layout.some((column) => column.includes(key))) return layout;
	const targetIndex = layout.findIndex((column) => column.length < MAX_PANELS_PER_COLUMN);
	if (targetIndex === -1) return [...layout, [key]];
	return layout.map((column, index) => (index === targetIndex ? [...column, key] : column));
}

/**
 * Remove a panel from the dock layout, dropping its column entirely if
 * it becomes empty.
 *
 * @param layout - The current column/panel arrangement.
 * @param key - The panel to close.
 * @returns A new layout array (the input is never mutated).
 */
function closePanelInLayout(layout: PanelKey[][], key: PanelKey): PanelKey[][] {
	return layout
		.map((column) => column.filter((panelKey) => panelKey !== key))
		.filter((column) => column.length > 0);
}

/**
 * The right-hand main panel of the chat page — every UI element that
 * operates on a single `(agentId, sessionId)` pair lives here:
 * model selector, permission mode select, message stream, workspace
 * drawer, and the team sidebar.
 *
 * Self-contained by design. The outer page passes in the
 * `(agentId, sessionId)` it wants displayed (which may be the leader
 * session or a focused team member's session) and this component
 * does the rest — fetching the session view, syncing local UI state
 * with it, and writing changes back to the same session. Switching
 * between leader and member is just a prop change; no internal
 * branching is needed.
 *
 * @param agentId - The agent to operate on. `null` while no agent is
 *   selected yet (renders an empty / disabled state).
 * @param sessionId - The session to operate on. `null` while no
 *   session is selected yet.
 * @returns The right-side main JSX of the chat page.
 */
export function ChatViewport({ agentId, sessionId, onTeamUpdated }: ChatViewportProps) {
	const { t } = useTranslation();
	const { sessions, refetch: refetchSessions } = useSessions(agentId);
	const { groups } = useAvailableModels();

	const [selectedModel, setSelectedModel] = useState<ChatModelConfig | null>(null);
	const [selectedFallbackModel, setSelectedFallbackModel] = useState<ChatModelConfig | null>(
		null,
	);
	const [selectedTTSModel, setSelectedTTSModel] = useState<TTSModelConfig | null>(null);
	const [selectedKnowledgeConfig, setSelectedKnowledgeConfig] =
		useState<SessionKnowledgeConfig | null>(null);
	const [selectedPermissionMode, setSelectedPermissionMode] = useState<string>('default');
	const [credentialOpen, setCredentialOpen] = useState(false);
	const [credentialRefetchTrigger, setCredentialRefetchTrigger] = useState(0);
	const [tasksContext, setTasksContext] = useState<TaskContext | null>(null);
	const [permissionContext, setPermissionContext] = useState<PermissionContext | null>(null);
	const [configPending, setConfigPending] = useState(false);
	// Dock layout: columns laid out left→right, each holding up to 2
	// panels stacked top→bottom. Open order determines placement.
	// Persisted so leaving and returning to /chat keeps the same panels.
	const [panelLayout, setPanelLayout] = useState<PanelKey[][]>(loadPanelLayout);

	useEffect(() => {
		localStorage.setItem(PANEL_LAYOUT_KEY, JSON.stringify(panelLayout));
	}, [panelLayout]);

	// When the viewport agent differs from the outer page's selected
	// agent (i.e. user drilled into a team member), `refetchSessions`
	// only refreshes the member's session list, so we also fire the
	// parent's refetch to keep its copy in sync.
	//
	// Surfacing the team panel here is what makes a team visible at all
	// — `TeamCreate` / `AgentCreate` / `AgentInvite` are agent tools, so
	// the user never opened a dialog that could have opened the panel.
	// `team_updated` also fires on `TeamDelete` and carries no payload,
	// hence checking the refetched list rather than opening blindly.
	const handleTeamUpdated = useCallback(async () => {
		const next = await refetchSessions();
		if (next.some((v) => v.session.id === sessionId && v.team)) {
			// `openPanelInLayout`, not `togglePanel` — the latter would
			// close a panel the user already has open.
			setPanelLayout((layout) => openPanelInLayout(layout, 'team'));
		}
		onTeamUpdated?.();
	}, [refetchSessions, sessionId, onTeamUpdated]);

	const handleStateUpdated = useCallback((value: Record<string, unknown>) => {
		if (value.tasks_context) {
			setTasksContext(value.tasks_context as TaskContext);
		}
		if (value.permission_context) {
			setPermissionContext(value.permission_context as PermissionContext);
		}
	}, []);

	const {
		msgs,
		loading: messagesLoading,
		phase,
		send,
		onUserConfirm,
		onSubagentConfirm,
		subagentHitl,
		interrupt,
	} = useMessages(agentId, sessionId, {
		onTeamUpdated: handleTeamUpdated,
		onStateUpdated: handleStateUpdated,
	});
	const {
		mcps,
		loading: mcpsLoading,
		addMcps,
		addMcpsFromLibrary,
		removeMcp,
		skills,
		skillsLoading,
		uploadSkill,
		addSkillsFromLibrary,
		removeSkill,
	} = useWorkspace(agentId, sessionId);
	const { knowledgeBases, loading: knowledgeBasesLoading } = useKnowledgeBases();
	const { schema: kbMiddlewareSchema } = useKnowledgeBaseMiddlewareSchema();

	// Toggle a panel open/closed from the top-bar buttons.
	const togglePanel = useCallback((key: PanelKey) => {
		setPanelLayout((layout) =>
			layout.some((column) => column.includes(key))
				? closePanelInLayout(layout, key)
				: openPanelInLayout(layout, key),
		);
	}, []);

	// Close a panel (driven by the panel's own close button).
	const closePanel = useCallback((key: PanelKey) => {
		setPanelLayout((layout) => closePanelInLayout(layout, key));
	}, []);

	const isPanelOpen = useCallback(
		(key: PanelKey) => panelLayout.some((column) => column.includes(key)),
		[panelLayout],
	);

	/**
	 * Persist a knowledge-base attachment change. `null` detaches every
	 * knowledge base from this session, removing the `RAGMiddleware`.
	 *
	 * Declared above `panels` (rather than alongside the other model
	 * handlers below) because `panels` is built inside `useMemo` and
	 * references this handler eagerly — a later `const` would still be
	 * in the temporal dead zone when the memo factory runs on first
	 * render.
	 *
	 * @param config - New attachment, or `null` to detach all.
	 */
	/**
	 * Persist a session config change, applying it locally only once
	 * the server accepts it.
	 *
	 * The backend rejects config writes with 409 while a chat run holds
	 * the session, so an optimistic update would leave the control
	 * showing a value the session does not have. Waiting for the
	 * response keeps the control on its previous value with no rollback
	 * bookkeeping; `client.ts` has already surfaced the error toast by
	 * the time we land in `catch`.
	 *
	 * Declared above `panels` for the same temporal-dead-zone reason as
	 * `handleKnowledgeConfigChange` below.
	 *
	 * @param body - The PATCH body.
	 * @param apply - Mirrors the change into local state on success.
	 */
	const patchConfig = useCallback(
		async (body: UpdateSessionRequest, apply: () => void) => {
			if (!sessionId || !agentId) return;
			setConfigPending(true);
			try {
				await sessionApi.update(sessionId, agentId, body);
				apply();
				await refetchSessions();
			} catch {
				// Toast already shown; local state deliberately untouched.
			} finally {
				setConfigPending(false);
			}
		},
		[sessionId, agentId, refetchSessions],
	);

	const handleKnowledgeConfigChange = useCallback(
		async (config: SessionKnowledgeConfig | null) => {
			await patchConfig({ knowledge_config: config }, () =>
				setSelectedKnowledgeConfig(config),
			);
		},
		[patchConfig],
	);

	// Declared above `panels` — the memo factory reads `view.team`
	// eagerly on first render, so a later `const` would still be in
	// the temporal dead zone.
	const view = sessions.find((v) => v.session.id === sessionId) ?? null;

	const { status: workspaceStatus, refetch: refetchWorkspaceStatus } = useWorkspaceStatus(
		agentId,
		sessionId,
		view?.session.config.cwd ?? null,
	);

	// A finished reply is the one moment the agent may have changed the
	// working tree, and it is why nothing polls for git status. Watching
	// `phase` rather than the REPLY_END event also covers the interrupt
	// timeout, which reaches idle without one.
	const prevPhaseRef = useRef(phase);
	useEffect(() => {
		const wasRunning = prevPhaseRef.current !== 'idle';
		prevPhaseRef.current = phase;
		if (wasRunning && phase === 'idle') void refetchWorkspaceStatus();
	}, [phase, refetchWorkspaceStatus]);

	// Build the panel descriptors with live data. Rebuilt on every
	// data change so the dock always renders the latest state — the
	// dock itself stays free of any data dependency.
	const panels = useMemo<Record<PanelKey, PanelDescriptor>>(
		() => ({
			plan: {
				title: t('panel.plan.title'),
				icon: <ListTodo className="size-4" />,
				content: <TaskPanel tasksContext={tasksContext} />,
			},
			mcp: {
				title: 'MCP',
				icon: <MCPSvg className="size-4" />,
				content: (
					<McpPanel
						mcps={mcps}
						loading={mcpsLoading}
						onAdd={addMcps}
						onAddFromLibrary={addMcpsFromLibrary}
						onRemove={removeMcp}
					/>
				),
			},
			skill: {
				title: t('panel.skill.title'),
				icon: <BookText className="size-4" />,
				content: (
					<SkillPanel
						skills={skills}
						loading={skillsLoading}
						onUpload={uploadSkill}
						onAddFromLibrary={addSkillsFromLibrary}
						onRemove={removeSkill}
					/>
				),
			},
			permission: {
				title: (
					<span className="flex items-center gap-x-2">
						{t('panel.permission.title')}
						{permissionContext?.mode ? (
							<Badge variant="outline" className="capitalize">
								{t('panel.permission.mode', { mode: permissionContext.mode })}
							</Badge>
						) : null}
					</span>
				),
				icon: <ShieldCheck className="size-4" />,
				content: <PermissionPanel permissionContext={permissionContext} />,
			},
			knowledge: {
				title: (
					<span className="flex items-center gap-x-2">
						{t('panel.knowledge.title')}
						{selectedKnowledgeConfig?.knowledge_base_ids.length ? (
							<Badge variant="outline">
								{selectedKnowledgeConfig.knowledge_base_ids.length}
							</Badge>
						) : null}
					</span>
				),
				icon: <Database className="size-4" />,
				actions: (
					<KnowledgeBaseParametersPopover
						value={selectedKnowledgeConfig}
						schema={kbMiddlewareSchema}
						onChange={handleKnowledgeConfigChange}
						disabled={!sessionId}
					/>
				),
				content: (
					<KnowledgeBasePanel
						knowledgeBases={knowledgeBases}
						loading={knowledgeBasesLoading}
						value={selectedKnowledgeConfig}
						onChange={handleKnowledgeConfigChange}
						disabled={!sessionId}
					/>
				),
			},
			team: {
				title: (
					<span className="flex items-center gap-x-2">
						{t('common.team')}
						{view?.team ? (
							<Badge variant="outline">{view.team.members.length}</Badge>
						) : null}
					</span>
				),
				icon: <UsersRound className="size-4" />,
				content: <TeamPanel team={view?.team ?? null} currentSessionId={sessionId} />,
			},
		}),
		[
			t,
			tasksContext,
			mcps,
			mcpsLoading,
			addMcps,
			addMcpsFromLibrary,
			removeMcp,
			skills,
			skillsLoading,
			uploadSkill,
			addSkillsFromLibrary,
			removeSkill,
			permissionContext,
			knowledgeBases,
			knowledgeBasesLoading,
			selectedKnowledgeConfig,
			kbMiddlewareSchema,
			handleKnowledgeConfigChange,
			sessionId,
			view,
		],
	);

	// ChatViewport keeps its own `useSessions(agentId)` instance (the
	// outer page has a separate one). Its built-in fetch only fires on
	// `agentId` change, so when the outer page creates a new session
	// under the same agent, this list doesn't auto-refresh. Without
	// this refetch, `view` would stay `null` for the brand-new session
	// id and every effect below would early-return on `!view`,
	// leaving the model select and friends pinned to whatever the
	// previously-viewed session had configured.
	useEffect(() => {
		if (!sessionId) return;
		if (view) return;
		refetchSessions();
	}, [sessionId, view, refetchSessions]);

	// Reset local UI state when the target session changes. Otherwise
	// the model select (and disabled-state guards on `send`) would
	// show the previous session's model during the in-flight window
	// before `view` repopulates — and an immediate send would post to
	// a session whose backend config doesn't actually have that model.
	useEffect(() => {
		setSelectedModel(null);
		setSelectedFallbackModel(null);
		setSelectedTTSModel(null);
		setSelectedKnowledgeConfig(null);
	}, [sessionId]);

	const selectedModelCard = useMemo(() => {
		if (!selectedModel) return null;
		const items = groups[selectedModel.type];
		if (!items) return null;
		for (const { models } of items) {
			const card = models.find((m) => m.name === selectedModel.model);
			if (card) return card;
		}
		return null;
	}, [groups, selectedModel?.type, selectedModel?.model]);

	/**
	 * Pick the first model the available-models endpoint surfaces, used
	 * as a sensible default when the current session has no model
	 * configured yet.
	 *
	 * @returns The first available `ChatModelConfig`, or `null` when
	 *   no credentials / models are configured.
	 */
	const getFirstAvailableModel = (): ChatModelConfig | null => {
		const firstType = Object.keys(groups)[0];
		if (!firstType) return null;
		const items = groups[firstType];
		if (!items || items.length === 0) return null;
		const firstItem = items[0];
		const firstModel = (firstItem.models as { name?: string; id?: string }[])[0];
		if (!firstModel) return null;
		const modelName = firstModel.name ?? firstModel.id ?? null;
		if (!modelName) return null;
		return {
			type: firstType,
			credential_id: firstItem.credential.id,
			model: modelName,
			parameters: {},
		};
	};

	// Seed tasks + permission from the session snapshot ONCE per
	// session, then leave them to the CustomEvent(name="state_updated")
	// stream via `handleStateUpdated`.
	//
	// Seeding on every `view` change would be wrong: storage is only
	// written when a run ends, so mid-run the snapshot still holds the
	// run-start values. `view` gets a new identity on every
	// `refetchSessions()` — which `team_updated` triggers — and
	// re-seeding then would silently roll both panels back to where the
	// reply started. Clearing on `!view` still matters so switching
	// sessions cannot leak the previous session's tasks or rules.
	const seededSessionRef = useRef<string | null>(null);
	useEffect(() => {
		if (!view) {
			seededSessionRef.current = null;
			setTasksContext(null);
			setPermissionContext(null);
			return;
		}
		if (seededSessionRef.current === view.session.id) return;
		seededSessionRef.current = view.session.id;
		const state = view.session.state as Record<string, unknown> | undefined;
		setTasksContext((state?.tasks_context as TaskContext) ?? null);
		setPermissionContext((state?.permission_context as PermissionContext) ?? null);
	}, [view]);

	// Sync selectedModel + selectedFallbackModel from the session
	// record. If the session has no model configured yet, auto-pick
	// the first available one and persist it back so subsequent
	// reasoning has a model to call.
	//
	// Important: skip while `view` is still loading. Otherwise the
	// in-flight window between "agentId changed" and "useSessions
	// returned the new list" looks like "session has no model" and
	// we would racily auto-select + persist the first available
	// model, clobbering whatever the user had configured.
	useEffect(() => {
		if (!view) return;
		const sessionModel = view.session.config.chat_model_config;

		if (sessionModel) {
			setSelectedModel(sessionModel);
		} else {
			const firstModel = getFirstAvailableModel();
			if (firstModel) {
				setSelectedModel(firstModel);
				if (sessionId && agentId) {
					// `silent` because the user did not ask for this write —
					// surfacing a toast for a revoked credential or a network
					// blip they never triggered is pure noise.
					sessionApi
						.update(
							sessionId,
							agentId,
							{ chat_model_config: firstModel },
							{ silent: true },
						)
						.then(() => refetchSessions())
						.catch(() => {});
				}
			} else {
				setSelectedModel(null);
			}
		}

		setSelectedFallbackModel(view.session.config.fallback_chat_model_config ?? null);
		setSelectedTTSModel(view.session.config.tts_model_config ?? null);
		setSelectedKnowledgeConfig(view.session.config.knowledge_config ?? null);
	}, [view, groups, sessionId, agentId]);

	// Sync selectedPermissionMode when the session changes. Same
	// loading-window guard as above — don't reset the displayed mode
	// to "default" while the new session view is still on the wire.
	useEffect(() => {
		if (!view) return;
		const mode = (view.session.state?.permission_context as Record<string, unknown>)
			?.mode as string;
		setSelectedPermissionMode(mode ?? 'default');
	}, [sessionId, view]);

	/**
	 * Persist a model change to the session and refetch so the local
	 * view picks up the new value.
	 *
	 * @param config - New chat model config; `null` is ignored
	 *   because the primary selector does not allow clearing.
	 */
	const handleLlmChange = async (config: ChatModelConfig | null) => {
		if (!config) return;
		await patchConfig({ chat_model_config: config }, () => setSelectedModel(config));
	};

	/**
	 * Persist a parameter change on the currently selected model.
	 *
	 * @param parameters - New parameter map (model-provider specific).
	 */
	const handleParametersChange = async (parameters: Record<string, unknown>) => {
		if (!selectedModel) return;
		const updated = { ...selectedModel, parameters };
		await patchConfig({ chat_model_config: updated }, () => setSelectedModel(updated));
	};

	/**
	 * Persist a fallback-model change. `null` clears the fallback.
	 *
	 * @param config - New fallback config or `null` to clear.
	 */
	const handleFallbackChange = async (config: ChatModelConfig | null) => {
		await patchConfig({ fallback_chat_model_config: config }, () =>
			setSelectedFallbackModel(config),
		);
	};

	/**
	 * Persist a TTS model change. `null` disables TTS.
	 *
	 * @param config - New TTS config or `null` to disable.
	 */
	const handleTTSChange = async (config: TTSModelConfig | null) => {
		await patchConfig({ tts_model_config: config }, () => setSelectedTTSModel(config));
	};

	/**
	 * Persist a permission-mode change.
	 *
	 * @param mode - New permission mode (e.g. `default`, `explore`).
	 */
	/**
	 * Persist a new working directory.
	 *
	 * Nothing local mirrors it — the value is read straight off the
	 * session view, which `patchConfig` refetches on success.
	 *
	 * @param next - Directory relative to the workspace root, or `null`
	 *   for the root itself.
	 */
	const handleCwdChange = async (next: string | null) => {
		// Bypasses `patchConfig`: the dialog shows the failure inline and
		// stays open on it, so the toast would be a duplicate and the
		// swallowed rejection would let the dialog close as if it worked.
		if (!sessionId || !agentId) return;
		setConfigPending(true);
		try {
			await sessionApi.update(sessionId, agentId, { cwd: next }, { silent: true });
			await refetchSessions();
		} finally {
			setConfigPending(false);
		}
	};

	const handlePermissionModeChange = async (mode: string) => {
		await patchConfig({ permission_mode: mode as PermissionMode }, () =>
			setSelectedPermissionMode(mode),
		);
	};

	return (
		<>
			<main className="flex size-full">
				<ResizablePanelGroup orientation="horizontal">
					<ResizablePanel
						className="flex flex-1 rounded-[22px] bg-card shadow-panel"
						minSize="24rem"
					>
						<div className="flex flex-col flex-1 min-h-0 min-w-0 overflow-x-hidden p-2">
							<div className="flex flex-row gap-x-2 justify-between">
								<div className="flex flex-row items-center gap-x-1">
									<SidebarTrigger className="md:hidden" />
								</div>
								<div className="flex flex-row gap-x-1">
									<LlmSelect
										id="tour-llm-select"
										variant="ghost"
										className="font-mono text-muted-foreground hover:text-foreground"
										value={selectedModel}
										onChange={handleLlmChange}
										onAddCredential={() => setCredentialOpen(true)}
										refetchTrigger={credentialRefetchTrigger}
										disabled={configPending}
									/>
									<ModelParametersPopover
										selectedModel={selectedModel}
										modelCard={selectedModelCard}
										onChange={handleParametersChange}
										selectedFallbackModel={selectedFallbackModel}
										onFallbackChange={handleFallbackChange}
										selectedTTSModel={selectedTTSModel}
										onTTSChange={handleTTSChange}
										disabled={configPending}
									/>
									<PermissionModeSelect
										id="tour-permission-mode"
										variant={'ghost'}
										className="font-mono text-muted-foreground hover:text-foreground"
										value={selectedPermissionMode}
										disabled={!sessionId || configPending}
										onChange={handlePermissionModeChange}
									/>
									<DropdownMenu>
										<DropdownMenuTrigger asChild>
											<Button
												variant="ghost"
												size="sm"
												className="gap-1 px-2"
											>
												<PanelRight />
												<ChevronDown className="size-3 text-muted-foreground" />
											</Button>
										</DropdownMenuTrigger>
										<DropdownMenuContent align="end" className="w-auto">
											<DropdownMenuCheckboxItem
												checked={isPanelOpen('plan')}
												onCheckedChange={() => togglePanel('plan')}
												onSelect={(e) => e.preventDefault()}
											>
												<ListTodo />
												{t('panel.plan.title')}
											</DropdownMenuCheckboxItem>
											<DropdownMenuCheckboxItem
												checked={isPanelOpen('mcp')}
												onCheckedChange={() => togglePanel('mcp')}
												onSelect={(e) => e.preventDefault()}
											>
												<MCPSvg className="size-4" />
												MCP
											</DropdownMenuCheckboxItem>
											<DropdownMenuCheckboxItem
												checked={isPanelOpen('skill')}
												onCheckedChange={() => togglePanel('skill')}
												onSelect={(e) => e.preventDefault()}
											>
												<BookText />
												{t('panel.skill.title')}
											</DropdownMenuCheckboxItem>
											<DropdownMenuCheckboxItem
												checked={isPanelOpen('permission')}
												onCheckedChange={() => togglePanel('permission')}
												onSelect={(e) => e.preventDefault()}
											>
												<ShieldCheck />
												{t('panel.permission.title')}
											</DropdownMenuCheckboxItem>
											<DropdownMenuCheckboxItem
												checked={isPanelOpen('knowledge')}
												onCheckedChange={() => togglePanel('knowledge')}
												onSelect={(e) => e.preventDefault()}
											>
												<Database />
												{t('panel.knowledge.title')}
											</DropdownMenuCheckboxItem>
											<DropdownMenuCheckboxItem
												checked={isPanelOpen('team')}
												onCheckedChange={() => togglePanel('team')}
												onSelect={(e) => e.preventDefault()}
											>
												<UsersRound />
												{t('common.team')}
											</DropdownMenuCheckboxItem>
										</DropdownMenuContent>
									</DropdownMenu>
								</div>
							</div>
							<div className="flex flex-1 justify-center min-h-0 overflow-hidden relative [--chat-content-w:48rem]">
								<ChatContent
									className={'max-w-[var(--chat-content-w)] w-full'}
									msgs={msgs}
									loading={messagesLoading}
									agentId={agentId}
									sessionId={sessionId}
									cwd={view?.session.config.cwd ?? null}
									onCwdChange={handleCwdChange}
									git={workspaceStatus?.git ?? null}
									onRefreshGit={refetchWorkspaceStatus}
									phase={phase}
									disabled={selectedModel === null}
									onSend={send}
									onUserConfirm={onUserConfirm}
									onInterrupt={interrupt}
									// cwd={
									// 	{cwd: view?.session.config.cwd, git: {
									// 		branch: 'main',
									// 		deletion: 0,
									// 		addition: 0,
									// 	}}
									// }
									footerSlot={
										subagentHitl.length > 0 ? (
											<SubagentHitlCard
												key={`${subagentHitl[0].worker_session_id}:${subagentHitl[0].reply_id}`}
												entry={subagentHitl[0]}
												onConfirm={(toolCall, confirm, rules) =>
													onSubagentConfirm(
														subagentHitl[0],
														toolCall,
														confirm,
														rules,
													)
												}
											/>
										) : null
									}
									allowedInputTypes={(
										selectedModelCard?.input_types ?? []
									).filter(
										(t) =>
											/^(image|video|audio|text)\/.+/.test(t) ||
											t === 'application/pdf' ||
											t.startsWith('application/vnd.') ||
											t.startsWith('application/msword') ||
											t.startsWith('application/vnd.openxmlformats'),
									)}
									fileProcessor={async (file) => {
										const filePath = (file as File & { path?: string }).path;
										if (filePath) {
											return {
												id: crypto.randomUUID(),
												type: 'data' as const,
												source: {
													type: 'url' as const,
													url: `file://${filePath}`,
													media_type:
														file.type || 'application/octet-stream',
												},
												name: file.name,
												created_at: new Date().toISOString(),
											};
										}
										if (file.type === 'text/plain') {
											const text = await file.text();
											return {
												id: crypto.randomUUID(),
												type: 'text' as const,
												text: `[File: ${file.name}]\n${text}`,
												created_at: new Date().toISOString(),
											};
										}
										const buffer = await file.arrayBuffer();
										const bytes = new Uint8Array(buffer);
										let binary = '';
										for (let i = 0; i < bytes.byteLength; i++) {
											binary += String.fromCharCode(bytes[i]);
										}
										const base64 = btoa(binary);
										return {
											id: crypto.randomUUID(),
											type: 'data' as const,
											source: {
												type: 'base64' as const,
												media_type: file.type || 'application/octet-stream',
												data: base64,
											},
											name: file.name,
											created_at: new Date().toISOString(),
										};
									}}
								/>
							</div>
						</div>
					</ResizablePanel>
					{panelLayout.length > 0 && (
						<ResizableHandle withHandle className="bg-transparent w-1.5" />
					)}
					<PanelDock layout={panelLayout} panels={panels} onClosePanel={closePanel} />
				</ResizablePanelGroup>
			</main>
			<CreateCredentialDialog
				open={credentialOpen}
				onOpenChange={setCredentialOpen}
				onCreated={() => setCredentialRefetchTrigger((n) => n + 1)}
			/>
		</>
	);
}
