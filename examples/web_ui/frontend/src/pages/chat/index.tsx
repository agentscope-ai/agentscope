import {
	BotMessageSquare,
	CalendarClock,
	Ellipsis,
	MessageSquareDashed,
	Pencil,
	Plus,
	Settings2,
	Trash2,
} from 'lucide-react';
import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

import { ChatViewport } from './ChatViewport';
import type { SessionRecord } from '@/api';
import { AgentDialog } from '@/components/dialog/AgentDialog';
import { DeleteDialog } from '@/components/dialog/DeleteDialog';
import { EditAgentDialog } from '@/components/dialog/EditAgentDialog';
import { RenameSessionDialog } from '@/components/dialog/RenameSessionDialog';
import { AgentSelect } from '@/components/select/AgentSelect';
import { TeamSidebar } from '@/components/team/TeamSidebar';
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
	const hasScheduleSessions = sessions.some((v) => v.session.source === 'schedule');

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

	return (
		<div className="flex h-full w-full">
			{/*
			 * Desktop stays `collapsible="none"` so the session list sits in
			 * normal flow beside the app rail (AppSidebar). Mobile switches to
			 * `offcanvas`, which makes shadcn's Sidebar render its Sheet overlay
			 * (the drawer we want) — instead of the desktop `fixed left-0`
			 * container, which would otherwise cover the app rail.
			 */}
			<Sidebar collapsible={isMobile ? 'offcanvas' : 'none'} className="border-r">
				<SidebarHeader>
					<div className="flex flex-col gap-y-2">
						<span className="text-muted-foreground text-xs">
							{localStorage.getItem('server_url')}
						</span>
						<div className="flex flex-row gap-x-2 items-center">
							<AgentSelect
								agents={agents}
								value={urlAgentId ?? null}
								onChange={(id) => navigate(`/chat/${id}`)}
							/>
							<Button
								size="icon"
								variant="ghost"
								disabled={!urlAgentId || !selectedAgent?.editable}
								tooltip={
									selectedAgent && !selectedAgent.editable
										? t('common.readOnlyTooltip')
										: undefined
								}
								onClick={() => setEditOpen(true)}
							>
								<Settings2 />
							</Button>
							<Button
								size="icon"
								variant="ghost"
								disabled={!urlAgentId || !selectedAgent?.editable}
								tooltip={
									selectedAgent && !selectedAgent.editable
										? t('common.readOnlyTooltip')
										: undefined
								}
								onClick={() => setDeleteOpen(true)}
							>
								<Trash2 className="text-destructive" />
							</Button>
						</div>
						<AgentDialog onCreated={refetchAgents} triggerId="tour-create-agent" />
					</div>
				</SidebarHeader>
				<SidebarContent className="my-5">
					<SidebarGroup>
						<SidebarGroupLabel>{t('chat.session.label')}</SidebarGroupLabel>
						<SidebarGroupAction asChild>
							<div>
								<Button
									id="tour-create-session"
									size="icon-xs"
									variant="default"
									disabled={!urlAgentId}
									onClick={handleCreateSession}
								>
									<Plus />
								</Button>
							</div>
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
											{urlAgentId
												? t('chat.session.emptyHasAgent')
												: t('chat.session.emptyNoAgent')}
										</EmptyDescription>
									</EmptyHeader>
									<EmptyContent>
										<Button
											variant="outline"
											size="sm"
											disabled={!urlAgentId}
											onClick={handleCreateSession}
										>
											Create Session
										</Button>
									</EmptyContent>
								</Empty>
							) : (
								<SidebarMenu>
									{sessions.map((view) => {
										const session = view.session;
										return (
											<SidebarMenuItem key={session.id}>
												<SidebarMenuButton
													isActive={urlSessionId === session.id}
													onClick={() => {
														navigate(
															`/chat/${urlAgentId}/${session.id}`,
														);
														setOpenMobile(false);
													}}
												>
													{hasScheduleSessions &&
														(session.source === 'schedule' ? (
															<CalendarClock />
														) : (
															<BotMessageSquare />
														))}
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
																	requestDeleteSession(session)
																}
															>
																<Trash2 />
																{t('session-menu.delete')}
															</DropdownMenuItem>
														</DropdownMenuContent>
													</DropdownMenu>
												</SidebarMenuAction>
											</SidebarMenuItem>
										);
									})}
								</SidebarMenu>
							)}
						</SidebarGroupContent>
					</SidebarGroup>
				</SidebarContent>
				<SidebarFooter />
			</Sidebar>
			{/*
			 * Team sidebar lives at the outer page level (not inside
			 * ChatViewport) so navigating between leader and member
			 * sessions does NOT unmount it. The team data comes from
			 * the leader's session view, which is stable across that
			 * navigation; only `currentSessionId` changes to drive
			 * row highlighting.
			 */}
			{currentView?.team && effectiveSessionId && (
				<TeamSidebar team={currentView.team} currentSessionId={effectiveSessionId} />
			)}
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
