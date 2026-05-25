import {
	Ellipsis,
	MessageSquareDashed,
	PanelLeft,
	PanelLeftClose,
	Pencil,
	Plus,
	Settings2,
	Toolbox,
	Trash2,
} from 'lucide-react';
import { useState, useEffect, useMemo } from 'react';
import { useParams } from 'react-router-dom';

import type { ChatModelConfig } from '@/api';
import type { SessionRecord } from '@/api';
import { ChatContent } from '@/components/chat/ChatContent.tsx';
import { AgentDialog } from '@/components/dialog/AgentDialog';
import { CreateCredentialDialog } from '@/components/dialog/CreateCredentialDialog';
import { DeleteAgentDialog } from '@/components/dialog/DeleteAgentDialog';
import { EditAgentDialog } from '@/components/dialog/EditAgentDialog';
import { RenameSessionDialog } from '@/components/dialog/RenameSessionDialog';
import { WorkspaceDrawer } from '@/components/drawer/WorkspaceDrawer.tsx';
import { ModelParametersPopover } from '@/components/popover/ModelParametersPopover';
import { LlmSelect } from '@/components/select/LlmSelect';
import { PermissionModeSelect } from '@/components/select/PermissionModeSelect.tsx';
import { ChatTourController } from '@/components/tour/ChatTourController';
import { Button } from '@/components/ui/button';
import {
	DropdownMenu,
	DropdownMenuContent,
	DropdownMenuItem,
	DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import {
	Empty,
	EmptyHeader,
	EmptyTitle,
	EmptyDescription,
	EmptyContent,
	EmptyMedia,
} from '@/components/ui/empty';
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from '@/components/ui/select';
import {
	Sidebar,
	SidebarContent,
	SidebarFooter,
	SidebarGroup,
	SidebarGroupAction,
	SidebarGroupContent,
	SidebarGroupLabel,
	SidebarHeader,
	SidebarMenu,
	SidebarMenuAction,
	SidebarMenuButton,
	SidebarMenuItem,
} from '@/components/ui/sidebar';
import { ChatProvider, useChatContext } from '@/context/ChatContext';
import { useAgents } from '@/hooks/useAgents';
import { useAvailableModels } from '@/hooks/useAvailableModels';
import { useMessages } from '@/hooks/useMessages';
import { useSessions } from '@/hooks/useSessions';
import { useWorkspace } from '@/hooks/useWorkspace.ts';
import { useTranslation } from '@/i18n/useI18n.ts';

const ChatPageInner = () => {
	const { selectedAgentId, setSelectedAgentId, selectedSessionId, setSelectedSessionId } =
		useChatContext();
	const { agentId: urlAgentId, sessionId: urlSessionId } = useParams<{
		agentId?: string;
		sessionId?: string;
	}>();
	const { t } = useTranslation();
	const { agents, refetch: refetchAgents } = useAgents();
	const {
		sessions,
		create: createSession,
		update: updateSession,
		remove: removeSession,
	} = useSessions(selectedAgentId);

	// Fetch available model list, used to auto-select the first model when none is specified
	const { groups } = useAvailableModels();

	const [sidebarOpen, setSidebarOpen] = useState(true);
	const [selectedModel, setSelectedModel] = useState<ChatModelConfig | null>(null);
	const [selectedPermissionMode, setSelectedPermissionMode] = useState<string>('default');
	const [editOpen, setEditOpen] = useState(false);
	const [deleteOpen, setDeleteOpen] = useState(false);
	const [credentialOpen, setCredentialOpen] = useState(false);
	const [credentialRefetchTrigger, setCredentialRefetchTrigger] = useState(0);
	const [renameOpen, setRenameOpen] = useState(false);
	const [renameSession, setRenameSession] = useState<SessionRecord | null>(null);

	const { msgs, streaming, send, onUserConfirm } = useMessages(
		selectedAgentId,
		selectedSessionId,
	);
	const {
		mcps,
		loading: mcpsLoading,
		addMcps,
		removeMcp,
		skills,
		skillsLoading,
		addSkill,
		removeSkill,
	} = useWorkspace(selectedAgentId, selectedSessionId);

	const selectedAgent = agents.find((a) => a.id === selectedAgentId) ?? null;

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

	// Auto-select agent on load — prefer URL param, fallback to first agent
	useEffect(() => {
		if (!selectedAgentId && agents.length > 0) {
			const target = urlAgentId && agents.find((a) => a.id === urlAgentId);
			setSelectedAgentId(target ? target.id : agents[0].id);
		}
	}, [agents, selectedAgentId, setSelectedAgentId, urlAgentId]);

	// Keep selectedSessionId in sync when sessions list changes — prefer URL param
	useEffect(() => {
		if (sessions.length === 0) {
			setSelectedSessionId(null);
		} else if (!sessions.find((s) => s.id === selectedSessionId)) {
			const target = urlSessionId && sessions.find((s) => s.id === urlSessionId);
			setSelectedSessionId(target ? target.id : sessions[0].id);
		}
	}, [sessions]);

	// Extract the first available model from groups, used as the fallback default selection.
	// groups shape: { [type]: { credential, models[] }[] }
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

	// Sync selectedModel with the current session's chat_model_config.
	// Re-runs when: the selected session changes, the sessions list changes,
	// or the available models list finishes loading.
	useEffect(() => {
		const session = sessions.find((s) => s.id === selectedSessionId);
		const sessionModel = session?.config.chat_model_config ?? null;

		if (sessionModel) {
			// Case 1: the current session already has a model configured — use it directly.
			setSelectedModel(sessionModel);
		} else {
			// Case 2: no model configured on the current session (or no session selected).
			// Try to auto-select the first available model to reduce manual work.
			const firstModel = getFirstAvailableModel();
			if (firstModel) {
				// A model is available — set it as the current selection.
				// Persistence notes:
				//   - If a session exists, persist the selection via updateSession below.
				//   - If no session exists yet, handleCreateSession will carry selectedModel on creation.
				setSelectedModel(firstModel);

				// If there is an active session without a model, persist the auto-selected model to it.
				if (selectedSessionId && selectedAgentId) {
					updateSession(selectedSessionId, { chat_model_config: firstModel });
				}
			} else {
				// Case 3: no model configured and no credentials added yet — clear the selection.
				setSelectedModel(null);
			}
		}
	}, [selectedSessionId, sessions, groups]);

	// Sync selectedPermissionMode when switching sessions.
	useEffect(() => {
		const session = sessions.find((s) => s.id === selectedSessionId);
		const mode = (session?.state?.permission_context as Record<string, unknown>)
			?.mode as string;
		setSelectedPermissionMode(mode ?? 'default');
	}, [selectedSessionId]);

	const handleLlmChange = async (config: ChatModelConfig) => {
		setSelectedModel(config);
		if (selectedSessionId && selectedAgentId) {
			await updateSession(selectedSessionId, { chat_model_config: config });
		}
	};

	const handleParametersChange = async (parameters: Record<string, unknown>) => {
		if (!selectedModel) return;
		const updated = { ...selectedModel, parameters };
		setSelectedModel(updated);
		if (selectedSessionId && selectedAgentId) {
			await updateSession(selectedSessionId, { chat_model_config: updated });
		}
	};

	const handleCreateSession = async () => {
		if (!selectedAgentId) return;
		const res = await createSession({
			agent_id: selectedAgentId,
			...(selectedModel ? { chat_model_config: selectedModel } : {}),
		});
		setSelectedSessionId(res.session_id);
	};

	const handleAgentDeleted = async () => {
		setSelectedAgentId(null);
		setSelectedSessionId(null);
		await refetchAgents();
	};

	const handleDeleteSession = async (sessionId: string) => {
		await removeSession(sessionId);
	};

	const handleRenameConfirm = async (name: string) => {
		if (!renameSession) return;
		await updateSession(renameSession.id, { name });
	};

	return (
		<div className="flex h-full w-full">
			{sidebarOpen && (
				<Sidebar collapsible="none" className="w-80">
					<SidebarHeader>
						<div className="flex flex-col gap-y-2">
							<span className="text-muted-foreground text-xs">
								{localStorage.getItem('server_url')}
							</span>
							<div className="flex flex-row gap-x-2 items-center">
								<Select
									value={selectedAgentId ?? ''}
									onValueChange={setSelectedAgentId}
								>
									<SelectTrigger className="w-full" size="sm">
										<SelectValue
											placeholder={t('chat.agent.selectPlaceholder')}
										/>
									</SelectTrigger>
									<SelectContent position="popper">
										{agents.length === 0 ? (
											<Empty className="border-none py-4">
												<EmptyHeader>
													<EmptyTitle>
														{t('chat.agent.emptyTitle')}
													</EmptyTitle>
													<EmptyDescription>
														{t('chat.agent.emptyDescription')}
													</EmptyDescription>
												</EmptyHeader>
											</Empty>
										) : (
											agents.map((agent) => (
												<SelectItem key={agent.id} value={agent.id}>
													{agent.data.name}
												</SelectItem>
											))
										)}
									</SelectContent>
								</Select>
								<Button
									size="icon-sm"
									variant="ghost"
									disabled={!selectedAgentId}
									onClick={() => setEditOpen(true)}
								>
									<Settings2 />
								</Button>
								<Button
									size="icon-sm"
									variant="ghost"
									disabled={!selectedAgentId}
									onClick={() => setDeleteOpen(true)}
								>
									<Trash2 className="text-destructive" />
								</Button>
							</div>
							<AgentDialog onCreated={refetchAgents} triggerId="tour-create-agent" />
						</div>
					</SidebarHeader>
					<SidebarContent className="my-5">
						{/*<SidebarGroup>*/}
						{/*	<SidebarGroupContent>*/}
						{/*		<Tabs defaultValue="mcp" onValueChange={() => {}}>*/}
						{/*			<TabsList className={'w-full'}>*/}
						{/*				<TabsTrigger value={'mcp'}>会话</TabsTrigger>*/}
						{/*				<TabsTrigger value={'skill'}>定时任务</TabsTrigger>*/}
						{/*			</TabsList>*/}
						{/*			<TabsContent value={'mcp'} asChild></TabsContent>*/}
						{/*		</Tabs>*/}
						{/*	</SidebarGroupContent>*/}
						{/*</SidebarGroup>*/}
						<SidebarGroup>
							<SidebarGroupLabel>{t('chat.session.label')}</SidebarGroupLabel>
							<SidebarGroupAction>
								<Button
									id="tour-create-session"
									size="icon-xs"
									variant="default"
									disabled={!selectedAgentId}
									onClick={handleCreateSession}
								>
									<Plus />
								</Button>
							</SidebarGroupAction>
							<SidebarGroupContent>
								{sessions.length === 0 ? (
									<Empty className="border-none py-4 min-h-50">
										<EmptyHeader>
											<EmptyMedia variant="icon">
												<MessageSquareDashed />
											</EmptyMedia>
											<EmptyTitle>{t('chat.session.emptyTitle')}</EmptyTitle>
											<EmptyDescription>
												{selectedAgentId
													? t('chat.session.emptyHasAgent')
													: t('chat.session.emptyNoAgent')}
											</EmptyDescription>
										</EmptyHeader>
										<EmptyContent>
											<Button
												variant="outline"
												size="sm"
												disabled={!selectedAgentId}
												onClick={handleCreateSession}
											>
												Create Session
											</Button>
										</EmptyContent>
									</Empty>
								) : (
									<SidebarMenu>
										{sessions.map((session) => (
											<SidebarMenuItem key={session.id}>
												<SidebarMenuButton
													isActive={selectedSessionId === session.id}
													onClick={() => setSelectedSessionId(session.id)}
												>
													<span className="truncate">
														{session.config.name || session.id}
													</span>
												</SidebarMenuButton>
												<SidebarMenuAction showOnHover>
													<DropdownMenu>
														<DropdownMenuTrigger asChild>
															<Ellipsis />
														</DropdownMenuTrigger>
														<DropdownMenuContent
															side="right"
															align="start"
														>
															<DropdownMenuItem
																onClick={() => {
																	setRenameSession(session);
																	setRenameOpen(true);
																}}
															>
																<Pencil />
																{t('session-menu.rename')}
															</DropdownMenuItem>
															<DropdownMenuItem
																variant="destructive"
																onClick={() =>
																	handleDeleteSession(session.id)
																}
															>
																<Trash2 />
																{t('session-menu.delete')}
															</DropdownMenuItem>
														</DropdownMenuContent>
													</DropdownMenu>
												</SidebarMenuAction>
											</SidebarMenuItem>
										))}
									</SidebarMenu>
								)}
							</SidebarGroupContent>
						</SidebarGroup>
					</SidebarContent>
					<SidebarFooter />
				</Sidebar>
			)}
			<main className="flex size-full pt-2">
				<Button
					variant="ghost"
					size="icon-sm"
					onClick={() => setSidebarOpen((prev) => !prev)}
					className="ml-2 mr-4"
				>
					{sidebarOpen ? (
						<PanelLeftClose className="size-4" />
					) : (
						<PanelLeft className="size-4" />
					)}
				</Button>
				<div className="flex flex-col flex-1 min-h-0">
					<div className="flex flex-row gap-x-2 justify-between">
						<div id="tour-llm-select" className="flex flex-row items-center gap-x-1">
							<LlmSelect
								value={selectedModel}
								onChange={handleLlmChange}
								onAddCredential={() => setCredentialOpen(true)}
								refetchTrigger={credentialRefetchTrigger}
							/>
							<ModelParametersPopover
								selectedModel={selectedModel}
								modelCard={selectedModelCard}
								onChange={handleParametersChange}
							/>
						</div>
						<div id="tour-permission-mode" className="flex flex-row gap-x-2">
							<PermissionModeSelect
								value={selectedPermissionMode}
								disabled={!selectedSessionId}
								onChange={(mode) => {
									setSelectedPermissionMode(mode);
									if (selectedSessionId) {
										updateSession(selectedSessionId, { permission_mode: mode });
									}
								}}
							/>
						</div>
					</div>
					<div className="flex flex-1 justify-center min-h-0 overflow-hidden">
						<ChatContent
							className={'max-w-xl'}
							msgs={msgs}
							sending={streaming}
							disabled={selectedModel === null}
							onSend={send}
							onUserConfirm={onUserConfirm}
							allowedInputTypes={(selectedModelCard?.input_types ?? []).filter(
								(t) =>
									/^(image|video|audio|text)\/.+/.test(t) ||
									t === 'application/pdf' ||
									t.startsWith('application/vnd.') ||
									t.startsWith('application/msword') ||
									t.startsWith('application/vnd.openxmlformats'),
							)}
							fileProcessor={async (file) => {
								const filePath = (file as File & { path?: string }).path;

								// ── Electron environment: have real local path ──
								if (filePath) {
									return {
										id: crypto.randomUUID(),
										type: 'data' as const,
										source: {
											type: 'url' as const,
											url: `file://${filePath}`,
											media_type: file.type || 'application/octet-stream',
										},
										name: file.name,
									};
								}

								// ── Browser environment: only File object in memory ──
								// text/plain → read as text, wrap in TextBlock
								if (file.type === 'text/plain') {
									const text = await file.text();
									// TODO: handle oversized text files — e.g. truncate, split into
									//  chunks, or warn the user when text.length exceeds the model's
									//  context window limit.
									return {
										id: crypto.randomUUID(),
										type: 'text' as const,
										text: `[File: ${file.name}]\n${text}`,
									};
								}

								// image/audio/video → read as base64, wrap in DataBlock
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
								};
							}}
						/>
					</div>
				</div>
				<div className="flex h-full px-2">
					<WorkspaceDrawer
						mcps={mcps}
						loading={mcpsLoading}
						onAdd={addMcps}
						onRemove={removeMcp}
						skills={skills}
						skillsLoading={skillsLoading}
						onAddSkill={addSkill}
						onRemoveSkill={removeSkill}
					>
						<Button size="icon-sm" variant="ghost">
							<Toolbox />
						</Button>
					</WorkspaceDrawer>
				</div>
			</main>
			{selectedAgent && (
				<>
					<EditAgentDialog
						open={editOpen}
						onOpenChange={setEditOpen}
						agent={selectedAgent}
						onUpdated={refetchAgents}
					/>
					<DeleteAgentDialog
						open={deleteOpen}
						onOpenChange={setDeleteOpen}
						agent={selectedAgent}
						onDeleted={handleAgentDeleted}
					/>
				</>
			)}
			<CreateCredentialDialog
				open={credentialOpen}
				onOpenChange={setCredentialOpen}
				onCreated={() => setCredentialRefetchTrigger((n) => n + 1)}
			/>
			<RenameSessionDialog
				open={renameOpen}
				onOpenChange={setRenameOpen}
				currentName={renameSession?.config.name ?? renameSession?.id ?? ''}
				onConfirm={handleRenameConfirm}
			/>
			<ChatTourController
				agentsCount={agents.length}
				sessionsCount={sessions.length}
				onEnsureSidebarOpen={() => setSidebarOpen(true)}
			/>
		</div>
	);
};

export const ChatPage = () => (
	<ChatProvider>
		<ChatPageInner />
	</ChatProvider>
);
