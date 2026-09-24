import { Bot, Crown, UsersRound } from 'lucide-react';
import { useNavigate } from 'react-router-dom';

import type { AgentView, TeamDetailResponse, TeamMemberInfo } from '@/api';
import { PanelEmpty } from '@/components/panel/PanelEmpty';
import { Button } from '@/components/ui/button';
import { useTranslation } from '@/i18n/useI18n';
import { cn } from '@/lib/utils';

interface TeamPanelProps {
	/**
	 * Resolved team detail (leader + members), or `null` when the open
	 * session is not part of a team.
	 */
	team: TeamDetailResponse | null;
	/**
	 * The session currently shown in the chat area — drives which row is
	 * highlighted as active.
	 */
	currentSessionId: string | null;
}

/**
 * Dock panel listing a team's leader and members, each row navigating
 * to that participant's session.
 *
 * Clicking the leader navigates to `/chat/<leaderAgentId>/<leaderSessionId>`,
 * dropping the URL's optional `:memberId` slot so the chat area falls
 * back to the leader session. Clicking a member appends that slot
 * instead — the outer URL segments stay put, so the page sidebar does
 * not collapse and only the chat area reroutes.
 *
 * Both ids come from the `team` prop, so the caller only has to say
 * which session is currently open.
 *
 * Renders without its own header or border — the surrounding `Panel`
 * chrome from `PanelDock` provides those.
 *
 * @param team - Resolved team detail, or `null` when there is no team.
 * @param currentSessionId - Session shown in the chat area.
 * @returns The panel body.
 */
export function TeamPanel({ team, currentSessionId }: TeamPanelProps) {
	const { t } = useTranslation();
	const navigate = useNavigate();

	if (!team) {
		return (
			<PanelEmpty
				icon={UsersRound}
				title={t('panel.team.emptyTitle')}
				description={t('panel.team.emptyDescription')}
			/>
		);
	}

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

	const renderRow = (
		key: string,
		icon: React.ReactNode,
		label: string,
		isActive: boolean,
		disabled: boolean,
		onClick: () => void,
	) => (
		<li key={key}>
			<Button
				variant="ghost"
				size="sm"
				disabled={disabled}
				onClick={onClick}
				className={cn(
					'w-full justify-start gap-2 font-normal',
					isActive && 'bg-accent text-accent-foreground',
				)}
			>
				{icon}
				<span className="truncate">{label}</span>
			</Button>
		</li>
	);

	const renderLeader = (leader: AgentView) =>
		renderRow(
			leader.id,
			<Crown className="size-3.5 shrink-0" />,
			leader.data.name,
			currentSessionId === leaderSessionId,
			false,
			goToLeader,
		);

	const renderMember = (member: TeamMemberInfo) =>
		renderRow(
			member.agent.id,
			<Bot className="size-3.5 shrink-0" />,
			member.agent.data.name,
			member.session_id === currentSessionId,
			member.session_id === null,
			() => goToMember(member.agent.id),
		);

	return (
		<div className="flex flex-col flex-1 min-h-0 gap-y-3 text-sm overflow-y-auto">
			<div className="shrink-0 px-2">
				<span className="truncate text-sm font-medium">{team.team.data.name}</span>
			</div>

			{team.leader_agent && (
				<section className="flex flex-col gap-y-0.5">
					<span className="px-2 text-xs text-muted-foreground">{t('common.leader')}</span>
					<ul className="flex flex-col">{renderLeader(team.leader_agent)}</ul>
				</section>
			)}

			<section className="flex flex-col gap-y-0.5">
				<span className="px-2 text-xs text-muted-foreground">
					{t('panel.team.membersHeading')}
				</span>
				{team.members.length === 0 ? (
					<p className="px-2 py-1 text-xs text-muted-foreground">
						{t('panel.team.noMembers')}
					</p>
				) : (
					<ul className="flex flex-col">{team.members.map(renderMember)}</ul>
				)}
			</section>
		</div>
	);
}
