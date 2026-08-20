import { format, isToday } from 'date-fns';
import {
	BotMessageSquare,
	Cable,
	CalendarClock,
	Ellipsis,
	type LucideIcon,
	MessageSquareDashed,
	Pencil,
	Plus,
	Settings2,
	Trash2,
} from 'lucide-react';
import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

import { ChatViewport } from './ChatViewport';
import type { SessionRecord, SessionSource } from '@/api';
import { AgentDialog } from '@/components/dialog/AgentDialog';
import { DeleteDialog } from '@/components/dialog/DeleteDialog';
import { EditAgentDialog } from '@/components/dialog/EditAgentDialog';
import { RenameSessionDialog } from '@/components/dialog/RenameSessionDialog';
import { AgentSelect } from '@/components/select/AgentSelect';
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
	EmptyMedia,
} from '@/components/ui/empty';
import {
	Sidebar,
	SidebarContent,
	SidebarGroup,
	SidebarGroupContent,
	SidebarGroupLabel,
	SidebarMenu,
	SidebarMenuAction,
	SidebarMenuBadge,
	SidebarMenuButton,
	SidebarMenuItem,
	SidebarProvider,
	useSidebar,
} from '@/components/ui/sidebar';
import { AudioProvider } from '@/context/AudioContext';
import { useAgents } from '@/hooks/useAgents';
import { useSessions } from '@/hooks/useSessions';
import { useTranslation } from '@/i18n/useI18n.ts';

/**
 * The chat page's outer shell. Responsibilities split cleanly:
 *
 * - **This component** owns *which* `(agent, session)` is being
 *   viewed. The URL is the single source of truth: every selection
 *   (agent dropdown, session row, team member, new session) is a
 *   ``navigate(...)`` call. State is derived from ``useParams``,
 *   never duplicated in React state. Renders the main left sidebar
 *   (agent picker + session list + create/rename/delete actions) and
 *   computes the ``effective`` ids to feed the chat viewport.
 * - **`ChatViewport`** owns *what* to render for that pair: messages,
 *   model selector, permission mode, workspace drawer, team sidebar.
 *
 * Splitting along this seam means switching between the leader's
 * session and a focused team member is just a prop change for the
 * viewport — the leader's session list stays anchored in this outer
 * sidebar. Driving everything off URL also gets us browser back /
 * forward, shareable links, and refresh-preserving state for free.
 *
 * @returns The chat page JSX.
 */
// Icon per session origin, shown only when a sidebar mixes sources.
const SOURCE_ICON: Record<SessionSource, LucideIcon> = {
	user: BotMessageSquare,
	schedule: CalendarClock,
	channel: Cable,
};

const ChatPageInner = () => {
	const navigate = useNavigate();
	const {
		agentId: urlAgentId,
		sessionId: urlSessionId,
		memberId: urlMemberId,
	} = useParams<{
		agentId?: string;
		sessionId?: string;
		memberId?: string;
	}>();
	const { t } = useTranslation();
	const { agents, refetch: refetchAgents, remove: removeAgent } = useAgents();
	const {
		sessions,
		refetch: refetchSessions,
		create: createSession,
		update: updateSession,
		remove: removeSession,
	} = useSessions(urlAgentId ?? null);

	const { isMobile, setOpen, setOpenMobile } = useSidebar();
	const [editOpen, setEditOpen] = useState(false);
	const [deleteOpen, setDeleteOpen] = useState(false);
	const [renameOpen, setRenameOpen] = useState(false);
	const [renameSession, setRenameSession] = useState<SessionRecord | null>(null);
	const [deleteSessionOpen, setDeleteSessionOpen] = useState(false);
	const [sessionToDelete, setSessionToDelete] = useState<SessionRecord | null>(null);

	const selectedAgent = agents.find((a) => a.id === urlAgentId) ?? null;
	const currentView = sessions.find((v) => v.session.id === urlSessionId) ?? null;
	// Show a per-origin icon only when sessions actually mix sources —
	// a uniform list needs no disambiguation.
	const showSourceIcons = new Set(sessions.map((v) => v.session.source)).size > 1;

	// "Inner focus" — when the URL carries a third `:memberId` segment
	// the user is drilling into a team member's chat. The main sidebar
	// stays anchored on the outer (leader) session; only the chat
	// viewport follows this inner focus. When `urlMemberId` is
	// undefined or doesn't resolve to a known team member, the inner
	// focus collapses back to the outer (leader) session.
	const focusedMember = urlMemberId
		? (currentView?.team?.members.find((m) => m.agent.id === urlMemberId) ?? null)
		: null;
	const effectiveAgentId =
		focusedMember && focusedMember.session_id ? focusedMember.agent.id : (urlAgentId ?? null);
	const effectiveSessionId =
		focusedMember && focusedMember.session_id
			? focusedMember.session_id
			: (urlSessionId ?? null);

	// Redirect: URL is missing an agent → pick the first one and rewrite
	// the URL in-place (replace so we don't pollute history).
	useEffect(() => {
		if (!urlAgentId && agents.length > 0) {
			navigate(`/chat/${agents[0].id}`, { replace: true });
		}
	}, [agents, urlAgentId, navigate]);

	// Redirect: URL has an agent but no session, or its sessionId no
	// longer exists for this agent → pick the first available session.
	useEffect(() => {
		if (!urlAgentId || sessions.length === 0) return;
		const matches = urlSessionId && sessions.some((v) => v.session.id === urlSessionId);
		if (matches) return;
		navigate(`/chat/${urlAgentId}/${sessions[0].session.id}`, { replace: true });
	}, [urlAgentId, urlSessionId, sessions, navigate]);

	/**
	 * Create a new session under the currently selected agent and
	 * pre-fill it with the model + fallback the currently open session
	 * is using (so "new chat" inherits whatever the user just had
	 * configured). Falls back to any other session under this agent
	 * when there is no current one — keeps the model choice sticky
	 * across "delete last → create new" instead of dropping back to
	 * whatever ChatViewport's auto-pick happens to land on. Navigates
	 * to the freshly created session.
	 */
	const handleCreateSession = async () => {
		if (!urlAgentId) return;
		const seedConfig = currentView?.session.config ?? sessions[0]?.session.config;
		const res = await createSession({
			agent_id: urlAgentId,
			...(seedConfig?.chat_model_config
				? { chat_model_config: seedConfig.chat_model_config }
				: {}),
			...(seedConfig?.fallback_chat_model_config
				? { fallback_chat_model_config: seedConfig.fallback_chat_model_config }
				: {}),
		});
		navigate(`/chat/${urlAgentId}/${res.session_id}`);
	};

	const handleAgentDeleted = async () => {
		navigate('/chat', { replace: true });
		await refetchAgents();
	};

	const handleDeleteSession = async (sessionId: string) => {
		await removeSession(sessionId);
		// If we just removed the session the URL is pointing at, fall
		// back to the parent /chat/:agentId path; the redirect effect
		// will then pick the next available session.
		if (sessionId === urlSessionId && urlAgentId) {
			navigate(`/chat/${urlAgentId}`, { replace: true });
		}
	};

	const requestDeleteSession = (session: SessionRecord) => {
		setSessionToDelete(session);
		setDeleteSessionOpen(true);
	};

	const handleRenameConfirm = async (name: string) => {
		if (!renameSession) return;
		await updateSession(renameSession.id, { name });
	};

	const todaySessions = sessions.filter((sess) => isToday(new Date(sess.session.created_at)));
	const earlierSessions = sessions.filter((sess) => !isToday(new Date(sess.session.created_at)));

	return (
		<div className="flex h-full w-full p-2 gap-2">
			{/*
			 * Desktop stays `collapsible="none"` so the session list sits in
			 * normal flow beside the app rail (AppSidebar). Mobile switches to
			 * `offcanvas`, which makes shadcn's Sidebar render its Sheet overlay
			 * (the drawer we want) — instead of the desktop `fixed left-0`
			 * container, which would otherwise cover the app rail.
			 */}
			<Sidebar collapsible={isMobile ? 'offcanvas' : 'none'} className="rounded-[22px]">
				{/* Scrolling moves down to the session list below, so the
				    agent picker and the new-session button stay put. */}
				<SidebarContent className="my-2 overflow-hidden">
					<SidebarGroup className="px-2 py-0">
						<SidebarGroupLabel className="justify-between">
							{t('common.agent')}
							<AgentDialog onCreated={refetchAgents}>
								<Button
									variant="ghost"
									size="icon-xs"
									title={t('dialog-agent-create.title')}
								>
									<Plus id="tour-create-agent" className="size-3.5" />
								</Button>
							</AgentDialog>
						</SidebarGroupLabel>
						<SidebarGroupContent className="flex items-center">
							<AgentSelect
								className="flex-1 min-w-0"
								agents={agents}
								value={urlAgentId ?? null}
								onChange={(id) => navigate(`/chat/${id}`)}
								variant="ghost"
								size="default"
							/>
							<DropdownMenu>
								<DropdownMenuTrigger asChild>
									<Button
										className="shrink-0 text-muted-foreground"
										variant="ghost"
										size="icon"
										disabled={!urlAgentId || !selectedAgent?.editable}
									>
										<Ellipsis />
									</Button>
								</DropdownMenuTrigger>
								{/* w-auto: the default pins the menu to the
								    trigger's width, which is a 32px icon button. */}
								<DropdownMenuContent className="w-auto">
									<DropdownMenuItem onClick={() => setEditOpen(true)}>
										<Settings2 />
										{t('agent-menu.settings')}
									</DropdownMenuItem>
									<DropdownMenuItem
										onClick={() => setDeleteOpen(true)}
										variant="destructive"
									>
										<Trash2 />
										{t('agent-menu.delete')}
									</DropdownMenuItem>
								</DropdownMenuContent>
							</DropdownMenu>
						</SidebarGroupContent>
					</SidebarGroup>
					<SidebarGroup className="mt-5 min-h-0 flex-1 px-2 py-0">
						<SidebarGroupLabel className="justify-between">
							{t('chat.session.label')}
							<span className="text-[10px] text-text-data font-mono">
								{sessions.length}
							</span>
						</SidebarGroupLabel>
						<SidebarGroupContent className="flex min-h-0 flex-1 flex-col">
							<SidebarGroup>
								<SidebarMenu className="mb-2">
									<Button id="tour-create-session" onClick={handleCreateSession}>
										<Plus />
										{t('chat.newSession')}
									</Button>
								</SidebarMenu>
							</SidebarGroup>

							<div className="no-scrollbar min-h-0 flex-1 overflow-y-auto">
								{sessions.length === 0 ? (
									<Empty className="border-none py-4 min-h-50">
										<EmptyHeader>
											<EmptyMedia variant="icon">
												<MessageSquareDashed />
											</EmptyMedia>
											<EmptyTitle>{t('chat.session.emptyTitle')}</EmptyTitle>
											<EmptyDescription>
												{urlAgentId
													? t('chat.session.emptyHasAgent')
													: t('chat.session.emptyNoAgent')}
											</EmptyDescription>
										</EmptyHeader>
									</Empty>
								) : (
									<>
										<SidebarGroup>
											<SidebarGroupLabel>
												{t('chat.session.today')}
											</SidebarGroupLabel>
											<SidebarGroupContent>
												<SidebarMenu>
													{todaySessions.map((view) => {
														const session = view.session;
														const SourceIcon =
															SOURCE_ICON[session.source] ??
															BotMessageSquare;
														return (
															<SidebarMenuItem key={session.id}>
																{/* Wider right gutter than the stock
															    pr-8: the badge holds a mono timestamp. */}
																<SidebarMenuButton
																	className="text-muted-foreground hover:text-foreground group-has-data-[sidebar=menu-action]/menu-item:pr-16"
																	isActive={
																		urlSessionId === session.id
																	}
																	onClick={() => {
																		navigate(
																			`/chat/${urlAgentId}/${session.id}`,
																		);
																		setOpenMobile(false);
																	}}
																>
																	{showSourceIcons && (
																		<SourceIcon />
																	)}
																	<span className="truncate">
																		{session.config.name ||
																			session.id}
																	</span>
																</SidebarMenuButton>
																{/* Badge and action are mutually exclusive.
															    Keyboard focus reveals the action, plain
															    focus-within does not — otherwise clicking
															    the row would pin it open. */}
																<SidebarMenuBadge className="max-md:hidden group-hover/menu-item:hidden group-has-focus-visible/menu-item:hidden group-has-data-[state=open]/menu-item:hidden text-text-tertiary! font-mono">
																	{format(
																		new Date(
																			view.session.created_at,
																		),
																		'HH:mm',
																	)}
																</SidebarMenuBadge>
																<DropdownMenu>
																	<DropdownMenuTrigger asChild>
																		<SidebarMenuAction className="md:opacity-0 group-hover/menu-item:opacity-100 group-has-focus-visible/menu-item:opacity-100 aria-expanded:opacity-100 peer-data-active/menu-button:text-sidebar-accent-foreground">
																			<Ellipsis />
																		</SidebarMenuAction>
																	</DropdownMenuTrigger>
																	<DropdownMenuContent
																		className="w-auto"
																		side="right"
																		align="start"
																	>
																		<DropdownMenuItem
																			onClick={() => {
																				setRenameSession(
																					session,
																				);
																				setRenameOpen(true);
																			}}
																		>
																			<Pencil />
																			{t(
																				'session-menu.rename',
																			)}
																		</DropdownMenuItem>
																		<DropdownMenuItem
																			variant="destructive"
																			onClick={() =>
																				requestDeleteSession(
																					session,
																				)
																			}
																		>
																			<Trash2 />
																			{t(
																				'session-menu.delete',
																			)}
																		</DropdownMenuItem>
																	</DropdownMenuContent>
																</DropdownMenu>
															</SidebarMenuItem>
														);
													})}
												</SidebarMenu>
											</SidebarGroupContent>
										</SidebarGroup>
										<SidebarGroup>
											<SidebarGroupLabel>
												{t('chat.session.earlier')}
											</SidebarGroupLabel>
											<SidebarGroupContent>
												<SidebarMenu>
													{earlierSessions.map((view) => {
														const session = view.session;
														const SourceIcon =
															SOURCE_ICON[session.source] ??
															BotMessageSquare;
														return (
															<SidebarMenuItem key={session.id}>
																<SidebarMenuButton
																	className="text-muted-foreground hover:text-foreground group-has-data-[sidebar=menu-action]/menu-item:pr-16"
																	isActive={
																		urlSessionId === session.id
																	}
																	onClick={() => {
																		navigate(
																			`/chat/${urlAgentId}/${session.id}`,
																		);
																		setOpenMobile(false);
																	}}
																>
																	{showSourceIcons && (
																		<SourceIcon />
																	)}
																	<span className="truncate">
																		{session.config.name ||
																			session.id}
																	</span>
																</SidebarMenuButton>
																<SidebarMenuBadge className="max-md:hidden group-hover/menu-item:hidden group-has-focus-visible/menu-item:hidden group-has-data-[state=open]/menu-item:hidden text-text-tertiary! font-mono">
																	{format(
																		new Date(
																			view.session.created_at,
																		),
																		'MMM dd',
																	)}
																</SidebarMenuBadge>
																<DropdownMenu>
																	<DropdownMenuTrigger asChild>
																		<SidebarMenuAction className="md:opacity-0 group-hover/menu-item:opacity-100 group-has-focus-visible/menu-item:opacity-100 aria-expanded:opacity-100 peer-data-active/menu-button:text-sidebar-accent-foreground">
																			<Ellipsis />
																		</SidebarMenuAction>
																	</DropdownMenuTrigger>
																	<DropdownMenuContent
																		className="w-auto"
																		side="right"
																		align="start"
																	>
																		<DropdownMenuItem
																			onClick={() => {
																				setRenameSession(
																					session,
																				);
																				setRenameOpen(true);
																			}}
																		>
																			<Pencil />
																			{t(
																				'session-menu.rename',
																			)}
																		</DropdownMenuItem>
																		<DropdownMenuItem
																			variant="destructive"
																			onClick={() =>
																				requestDeleteSession(
																					session,
																				)
																			}
																		>
																			<Trash2 />
																			{t(
																				'session-menu.delete',
																			)}
																		</DropdownMenuItem>
																	</DropdownMenuContent>
																</DropdownMenu>
															</SidebarMenuItem>
														);
													})}
												</SidebarMenu>
											</SidebarGroupContent>
										</SidebarGroup>
									</>
								)}
							</div>
						</SidebarGroupContent>
					</SidebarGroup>
				</SidebarContent>
			</Sidebar>
			<div className="flex flex-1 min-w-0">
				<ChatViewport
					agentId={effectiveAgentId}
					sessionId={effectiveSessionId}
					onTeamUpdated={refetchSessions}
				/>
			</div>
			{selectedAgent && (
				<>
					<EditAgentDialog
						open={editOpen}
						onOpenChange={setEditOpen}
						agent={selectedAgent}
						onUpdated={refetchAgents}
					/>
					<DeleteDialog
						open={deleteOpen}
						onOpenChange={setDeleteOpen}
						title={t('common.deleteTitle', {
							entity: t('dialog-agent-delete.entity'),
							name: selectedAgent.data.name,
						})}
						description={t('common.deleteDescription')}
						confirmLabel={t('dialog-agent-delete.confirm')}
						onConfirm={async () => {
							await removeAgent(selectedAgent.id);
							await handleAgentDeleted();
						}}
					/>
				</>
			)}
			<RenameSessionDialog
				open={renameOpen}
				onOpenChange={setRenameOpen}
				currentName={renameSession?.config.name ?? renameSession?.id ?? ''}
				onConfirm={handleRenameConfirm}
			/>
			<DeleteDialog
				open={deleteSessionOpen}
				onOpenChange={setDeleteSessionOpen}
				title={t('common.deleteTitle', {
					entity: t('dialog-session-delete.entity'),
					name: (() => {
						const raw = sessionToDelete?.config.name || sessionToDelete?.id || '';
						return raw.length > 30 ? `${raw.slice(0, 30)}…` : raw;
					})(),
				})}
				description={t('common.deleteDescription')}
				confirmLabel={t('dialog-session-delete.confirm')}
				onConfirm={async () => {
					if (sessionToDelete) {
						await handleDeleteSession(sessionToDelete.id);
					}
				}}
			/>
			<ChatTourController
				agentsCount={agents.length}
				sessionsCount={sessions.length}
				onEnsureSidebarOpen={() => {
					setOpen(true);
					setOpenMobile(true);
				}}
			/>
		</div>
	);
};

export const ChatPage = () => (
	<AudioProvider>
		<SidebarProvider defaultOpen>
			<ChatPageInner />
		</SidebarProvider>
	</AudioProvider>
);
