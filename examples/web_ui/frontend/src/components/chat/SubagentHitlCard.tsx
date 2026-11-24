import type { ToolCallBlock } from '@agentscope-ai/agentscope/message';
import { Users } from 'lucide-react';
import { useState } from 'react';

import { ConfirmCard } from './ConfirmCard';
import { ExternalExecutionCard } from './ExternalExecutionCard';
import type { SubagentHitlEntry } from '@/hooks/useMessages';
import { useTranslation } from '@/i18n/useI18n';
import type { ClientExternalToolResult } from '@/lib/client-external-tools';

/** Render the member's pending interaction on the leader session. */
export function SubagentHitlCard({
	entry,
	onResult,
	onConfirm,
}: {
	entry: SubagentHitlEntry;
	onResult: (
		call: ToolCallBlock,
		result: ClientExternalToolResult,
		state?: 'success' | 'interrupted',
	) => Promise<void>;
	onConfirm: (
		toolCall: ToolCallBlock,
		confirm: boolean,
		rules?: ToolCallBlock['suggested_rules'],
	) => Promise<void>;
}) {
	const { t } = useTranslation();
	const [cancelling, setCancelling] = useState<Set<string>>(() => new Set());
	const toolCalls = entry.event.tool_calls ?? [];

	if (toolCalls.length === 0) return null;

	return (
		<div className="ring ring-border rounded-[28px] w-full p-3 space-y-3 bg-card">
			<div className="flex items-center gap-2 text-sm font-medium text-secondary-foreground px-2">
				<Users className="size-4 shrink-0" />
				<span>{t('chat.subagentConfirmTitle', { name: entry.worker_agent_name })}</span>
			</div>
			<div className="space-y-2">
				{toolCalls.map((toolCall) =>
					entry.event_type === 'require_external_execution' ? (
						<ExternalExecutionCard
							key={JSON.stringify([entry.event.id, toolCall.id])}
							toolCall={toolCall}
							autoFocus={false}
							onSubmit={(result) => onResult(toolCall, result)}
							cancelling={cancelling.has(toolCall.id)}
							onCancel={async () => {
								setCancelling((prev) => new Set(prev).add(toolCall.id));
								try {
									await onResult(toolCall, '', 'interrupted');
								} catch {
									setCancelling((prev) => {
										const next = new Set(prev);
										next.delete(toolCall.id);
										return next;
									});
								}
							}}
						/>
					) : (
						<ConfirmCard
							key={JSON.stringify([entry.event.id, toolCall.id])}
							toolCall={toolCall}
							onUserConfirm={(confirm, rules) => onConfirm(toolCall, confirm, rules)}
						/>
					),
				)}
			</div>
		</div>
	);
}
