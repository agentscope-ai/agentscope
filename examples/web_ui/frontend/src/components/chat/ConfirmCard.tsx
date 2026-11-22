import type { ToolCallBlock } from '@agentscope-ai/agentscope/message';
import { ChevronRight } from 'lucide-react';
import { useRef, useState } from 'react';

import { getDisplayName, renderConfirmBody } from './tool-renderers';
import { Button } from '@/components/ui/button';
import { Kbd } from '@/components/ui/kbd';
import { Spinner } from '@/components/ui/spinner.tsx';
import { useTranslation } from '@/i18n/useI18n';
import { cn } from '@/lib/utils';

type SelectOption = 'yes' | 'yes_with_rule' | 'no';

const OPTIONS_WITH_SUGGESTED_RULES: SelectOption[] = ['yes', 'yes_with_rule', 'no'];
const OPTIONS_WITHOUT_SUGGESTED_RULES: SelectOption[] = ['yes', 'no'];

export function ConfirmCard({
	toolCall,
	onUserConfirm,
}: {
	toolCall: ToolCallBlock & { confirmation_message?: string };
	onUserConfirm: (confirm: boolean, rules?: ToolCallBlock['suggested_rules']) => Promise<void>;
}) {
	const { t } = useTranslation();
	const hasSuggestedRules = !!toolCall.suggested_rules?.length;
	const options = hasSuggestedRules
		? OPTIONS_WITH_SUGGESTED_RULES
		: OPTIONS_WITHOUT_SUGGESTED_RULES;
	const [selected, setSelected] = useState<SelectOption | null>(null);
	const [hasConfirmed, setHasConfirmed] = useState<boolean>(false);
	const submittingRef = useRef(false);

	async function handleConfirm(option: SelectOption) {
		if (submittingRef.current) return;
		submittingRef.current = true;
		setSelected(option);
		setHasConfirmed(true);
		try {
			const rules = option === 'yes_with_rule' ? [toolCall.suggested_rules![0]] : undefined;
			await onUserConfirm(option !== 'no', rules);
		} catch {
			submittingRef.current = false;
			setHasConfirmed(false);
		}
	}

	return (
		<div className="bg-muted ring ring-border rounded-[28px] w-full px-6 py-5 space-y-4 text-sm overflow-hidden">
			<div className="flex flex-col gap-y-2 max-w-full">
				<strong className="text-secondary-foreground">{getDisplayName(toolCall, t)}</strong>
				{toolCall.confirmation_message && <p>{toolCall.confirmation_message}</p>}
				{/* Capped so a long tool input (raw JSON for MCP tools) cannot
				    grow the card past the viewport — it floats above the
				    composer and is out of flow, so nothing else clips it. */}
				<div className="px-4 py-2 bg-background rounded-sm max-w-full max-h-[200px] overflow-y-auto">
					{renderConfirmBody(toolCall, t)}
				</div>
			</div>
			<div className="flex flex-col">
				<strong className="text-secondary-foreground mb-1">
					{t('chat.confirmToolCall')}
				</strong>
				{options.map((option, index) => (
					<Button
						key={option}
						type="button"
						className={cn(
							'flex justify-start cursor-pointer',
							selected === option ? 'text-primary' : 'text-muted-foreground',
						)}
						size="sm"
						variant="ghost"
						onFocus={() => setSelected(option)}
						onBlur={() => {
							if (!submittingRef.current) setSelected(null);
						}}
						onKeyDown={(e) => {
							const activation = e.key === 'Enter' || e.key === ' ';
							if (
								e.nativeEvent.isComposing ||
								e.nativeEvent.keyCode === 229 ||
								e.repeat ||
								e.shiftKey ||
								e.ctrlKey ||
								e.altKey ||
								e.metaKey
							) {
								if (activation) e.preventDefault();
								return;
							}
							if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
								e.preventDefault();
								const buttons =
									e.currentTarget.parentElement?.querySelectorAll('button');
								const delta = e.key === 'ArrowDown' ? 1 : -1;
								buttons?.[
									(index + delta + options.length) % options.length
								]?.focus();
							}
						}}
						onClick={(e) => {
							e.stopPropagation();
							void handleConfirm(option);
						}}
						disabled={hasConfirmed}
					>
						{hasConfirmed && selected === option ? (
							<Spinner className="size-4 shrink-0" />
						) : (
							<ChevronRight
								className={cn(
									'size-4 shrink-0',
									selected !== option && 'invisible',
								)}
							/>
						)}
						<span className="break-all min-w-0 whitespace-normal">
							{index + 1}.{' '}
							{option === 'yes_with_rule'
								? t('confirmCard.yesWithRule', {
										toolName: toolCall.suggested_rules![0].tool_name,
										ruleContent: toolCall.suggested_rules![0].rule_content,
									})
								: t(option === 'yes' ? 'common.yes' : 'common.no')}
							{selected === option && (
								<span className="text-muted-foreground ml-1 whitespace-nowrap">
									(<Kbd>Enter</Kbd> {t('confirmCard.toConfirm')})
								</span>
							)}
						</span>
					</Button>
				))}
			</div>
		</div>
	);
}
