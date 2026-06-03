import { Bot, Crown } from 'lucide-react';
import { useNavigate } from 'react-router-dom';

import type { TeamDetailResponse, TeamMemberInfo, AgentRecord } from '@/api';
import {
	Sidebar,
	SidebarContent,
	SidebarFooter,
	SidebarGroup,
	SidebarGroupContent,
	SidebarGroupLabel,
	SidebarHeader,
	SidebarMenu,
	SidebarMenuButton,
	SidebarMenuItem,
} from '@/components/ui/sidebar';
import { useTranslation } from '@/i18n/useI18n';

interface TeamSidebarProps {
	/** Resolved team detail (leader + members) — drives all rendering. */
	team: TeamDetailResponse;
	/**
	 * The session id currently shown in the chat area. Used both for
	 * highlighting the active row and for deciding whether the row
	 * being clicked is already the current one.
	 */
	currentSessionId: string;
}

/**
 * Secondary sidebar shown next to the chat area whenever the open
 * session participates in a team.
 *
 * Built on the shared shadcn `Sidebar` primitives so it visually
 * matches the main page sidebar (same paddings, item heights,
 * active-row treatment, etc.). Renders the leader at the top
 * followed by every member; all rows are clickable.
 *
 * Clicking the leader row navigates to
 * `/chat/<leaderAgentId>/<leaderSessionId>` (drops the URL's
 * optional `:memberId` slot so the chat area falls back to the
 * leader session). Clicking a member row navigates to
 * `/chat/<leaderAgentId>/<leaderSessionId>/<memberAgentId>` — the
 * outer URL slots stay the same so the main page sidebar does not
 * collapse; only the chat area reroutes to the member's session.
 *
 * Both `leaderAgentId` and `leaderSessionId` are derived from the
 * passed `team` prop, so the parent only needs to know which
 * session is currently active.
 *
 * @param team - Resolved team detail.
 * @param currentSessionId - Session id currently shown in the chat
 *   area; used to drive row highlighting.
 * @returns A vertical sidebar element.
 */
export function TeamSidebar({ team, currentSessionId }: TeamSidebarProps) {
	const { t } = useTranslation();
	const navigate = useNavigate();

	const leaderAgentId = team.leader_agent?.id ?? null;
	const leaderSessionId = team.team.session_id;

	const goToLeader = () => {
		if (!leaderAgentId) return;
		navigate(`/chat/${leaderAgentId}/${leaderSessionId}`);
	};

	const goToMember = (memberAgentId: string) => {
		if (!leaderAgentId) return;
		navigate(`/chat/${leaderAgentId}/${leaderSessionId}/${memberAgentId}`);
	};

	const renderLeader = (leader: AgentRecord) => (
		<SidebarMenuItem>
			<SidebarMenuButton isActive={currentSessionId === leaderSessionId} onClick={goToLeader}>
				<Crown />
				<span className="truncate">{leader.data.name}</span>
			</SidebarMenuButton>
		</SidebarMenuItem>
	);

	const renderMember = (member: TeamMemberInfo) => (
		<SidebarMenuItem key={member.agent.id}>
			<SidebarMenuButton
				isActive={member.session_id === currentSessionId}
				disabled={member.session_id === null}
				onClick={() => goToMember(member.agent.id)}
			>
				<Bot />
				<span className="truncate">{member.agent.data.name}</span>
			</SidebarMenuButton>
		</SidebarMenuItem>
	);

	return (
		<Sidebar collapsible="none" className="w-56 border-r">
			<SidebarHeader>
				<div className="flex flex-col gap-y-1 px-2 py-1">
					<span className="text-muted-foreground text-xs uppercase tracking-wide">
						{t('common.team')}
					</span>
					<span className="truncate text-sm font-medium">{team.team.data.name}</span>
				</div>
			</SidebarHeader>
			<SidebarContent>
				{team.leader_agent && (
					<SidebarGroup>
						<SidebarGroupLabel>{t('common.leader')}</SidebarGroupLabel>
						<SidebarGroupContent>
							<SidebarMenu>{renderLeader(team.leader_agent)}</SidebarMenu>
						</SidebarGroupContent>
					</SidebarGroup>
				)}

				<SidebarGroup>
					<SidebarGroupLabel>{t('team-sidebar.membersHeading')}</SidebarGroupLabel>
					<SidebarGroupContent>
						{team.members.length === 0 ? (
							<p className="px-3 py-2 text-xs text-muted-foreground">
								{t('team-sidebar.noMembers')}
							</p>
						) : (
							<SidebarMenu>{team.members.map(renderMember)}</SidebarMenu>
						)}
					</SidebarGroupContent>
				</SidebarGroup>
			</SidebarContent>
			<SidebarFooter />
		</Sidebar>
	);
}
