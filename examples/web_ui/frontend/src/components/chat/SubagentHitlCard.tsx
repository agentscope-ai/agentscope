import type { ToolCallBlock } from '@agentscope-ai/agentscope/message';
import { Users } from 'lucide-react';

import { ConfirmCard } from './ConfirmCard';
import type { SubagentHitlEntry } from '@/hooks/useMessages';
import { useTranslation } from '@/i18n/useI18n';

/**
 * A pending HITL confirmation raised by a team *member* (worker)
 * session, surfaced on the *leader* view so the user can answer it
 * without drilling into the member's own session.
 *
 * Renders one {@link ConfirmCard} per pending tool call, reusing the
 * exact same confirm UI (and suggested-rules interaction) as a
 * first-party confirmation. The only addition is a header naming the
 * member that is asking.
 *
 * Confirmation is routed through {@link onConfirm}, which the parent
 * wires to ``useMessages``' ``onSubagentConfirm`` — POSTing the result
 * to the leader front door, where the backend forwards it to the
 * worker session (design §3.6).
 */
export function SubagentHitlCard({
	entry,
	onConfirm,
}: {
	entry: SubagentHitlEntry;
	onConfirm: (
		toolCall: ToolCallBlock,
		confirm: boolean,
		rules?: ToolCallBlock['suggested_rules'],
	) => void;
}) {
	const { t } = useTranslation();
	const toolCalls = entry.event.tool_calls ?? [];

	if (toolCalls.length === 0) return null;

	return (
		<div className="ring ring-border rounded-xl w-full p-3 space-y-3 bg-secondary/30">
			<div className="flex items-center gap-2 text-sm font-medium text-secondary-foreground">
				<Users className="size-4 shrink-0" />
				<span>{t('chat.subagentConfirmTitle', { name: entry.worker_agent_name })}</span>
			</div>
			<div className="space-y-2">
				{toolCalls.map((toolCall) => (
					<ConfirmCard
						key={toolCall.id}
						toolCall={toolCall}
						onUserConfirm={(confirm, rules) => onConfirm(toolCall, confirm, rules)}
					/>
				))}
			</div>
		</div>
	);
}
