import type { ToolCallBlock } from '@agentscope-ai/agentscope/message';
import { ChevronRight } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';

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
	toolCall: ToolCallBlock;
	onUserConfirm: (confirm: boolean, rules?: ToolCallBlock['suggested_rules']) => Promise<void>;
}) {
	const { t } = useTranslation();
	const hasSuggestedRules = !!toolCall.suggested_rules?.length;
	const options = hasSuggestedRules
		? OPTIONS_WITH_SUGGESTED_RULES
		: OPTIONS_WITHOUT_SUGGESTED_RULES;
	const [selected, setSelected] = useState<SelectOption>('yes');
	const [hasConfirmed, setHasConfirmed] = useState<boolean>(false);
	const submittingRef = useRef(false);

	const handleConfirm = useCallback(
		async (confirm: boolean, rules?: ToolCallBlock['suggested_rules']) => {
			if (submittingRef.current) return;
			submittingRef.current = true;
			setHasConfirmed(true);
			try {
				await onUserConfirm(confirm, rules);
			} catch {
				submittingRef.current = false;
				setHasConfirmed(false);
			}
		},
		[onUserConfirm],
	);

	useEffect(() => {
		const handleKeyDown = async (e: KeyboardEvent) => {
			const currentIndex = options.indexOf(selected);
			switch (e.key) {
				case 'ArrowUp':
					e.preventDefault();
					setSelected(options[(currentIndex - 1 + options.length) % options.length]);
					break;
				case 'ArrowDown':
					e.preventDefault();
					setSelected(options[(currentIndex + 1) % options.length]);
					break;
				case 'Enter':
					e.preventDefault();
					if (selected === 'yes_with_rule') {
						await handleConfirm(true, [toolCall.suggested_rules![0]]);
					} else {
						await handleConfirm(selected === 'yes');
					}
					break;
			}
		};

		window.addEventListener('keydown', handleKeyDown);
		return () => window.removeEventListener('keydown', handleKeyDown);
	}, [handleConfirm, selected, options, toolCall.suggested_rules]);

	return (
		<div className="bg-muted ring ring-border rounded-[28px] w-full px-6 py-5 space-y-4 text-sm overflow-hidden">
			<div className="flex flex-col gap-y-2 max-w-full">
				<strong className="text-secondary-foreground">{getDisplayName(toolCall, t)}</strong>
				{/* Capped so a long tool input (raw JSON for MCP tools) cannot
				    grow the card past the viewport — it floats above the
				    composer and is out of flow, so nothing else clips it. */}
				<div className="px-4 py-2 bg-white rounded-sm max-w-full max-h-[200px] overflow-y-auto">
					{renderConfirmBody(toolCall, t)}
				</div>
			</div>
			<div className="flex flex-col">
				<strong className="text-secondary-foreground mb-1">
					{t('chat.confirmToolCall')}
				</strong>
				<Button
					className={cn(
						'flex justify-start cursor-pointer',
						selected === 'yes' ? 'text-primary' : 'text-muted-foreground',
					)}
					size="sm"
					variant="ghost"
					onMouseEnter={() => setSelected('yes')}
					onClick={async (e) => {
						e.stopPropagation();
						e.preventDefault();
						await handleConfirm(true);
					}}
					disabled={hasConfirmed}
				>
					{hasConfirmed ? (
						<Spinner
							className={cn('size-4', selected === 'yes' ? 'visible' : 'invisible')}
						/>
					) : (
						<ChevronRight
							className={cn('size-4', selected === 'yes' ? 'visible' : 'invisible')}
						/>
					)}
					1. {t('common.yes')}
					<div className={cn(selected === 'yes' ? 'text-muted-foreground' : 'invisible')}>
						(<Kbd>Enter</Kbd> {t('confirmCard.toConfirm')})
					</div>
				</Button>
				{hasSuggestedRules && (
					<Button
						className={cn(
							'flex flex-wrap justify-start items-start cursor-pointer h-auto text-left',
							selected === 'yes_with_rule' ? 'text-primary' : 'text-muted-foreground',
						)}
						size="sm"
						variant="ghost"
						onMouseEnter={() => setSelected('yes_with_rule')}
						onClick={async (e) => {
							e.stopPropagation();
							e.preventDefault();
							await handleConfirm(true, [toolCall.suggested_rules![0]]);
						}}
						disabled={hasConfirmed}
					>
						<span className="flex items-start gap-1 w-full break-words whitespace-normal min-w-0">
							{hasConfirmed ? (
								<Spinner
									className={cn(
										'size-4 shrink-0 mt-0.5',
										selected === 'yes_with_rule' ? 'visible' : 'invisible',
									)}
								/>
							) : (
								<ChevronRight
									className={cn(
										'size-4 shrink-0 mt-0.5',
										selected === 'yes_with_rule' ? 'visible' : 'invisible',
									)}
								/>
							)}
							<span className="break-all min-w-0">
								2.{' '}
								{t('confirmCard.yesWithRule', {
									toolName: toolCall.suggested_rules![0].tool_name,
									ruleContent: toolCall.suggested_rules![0].rule_content,
								})}
								<span
									className={cn(
										'text-muted-foreground ml-1 whitespace-nowrap',
										selected === 'yes_with_rule' ? 'visible' : 'invisible',
									)}
								>
									(<Kbd>Enter</Kbd> {t('confirmCard.toConfirm')})
								</span>
							</span>
						</span>
					</Button>
				)}
				<Button
					className={cn(
						'flex justify-start cursor-pointer',
						selected === 'no' ? 'text-primary' : 'text-muted-foreground',
					)}
					size="sm"
					variant="ghost"
					onMouseEnter={() => setSelected('no')}
					onClick={async (e) => {
						e.stopPropagation();
						e.preventDefault();
						await handleConfirm(false);
					}}
					disabled={hasConfirmed}
				>
					{hasConfirmed ? (
						<Spinner
							className={cn('size-4', selected === 'no' ? 'visible' : 'invisible')}
						/>
					) : (
						<ChevronRight
							className={cn('size-4', selected === 'no' ? 'visible' : 'invisible')}
						/>
					)}
					{hasSuggestedRules ? '3' : '2'}. {t('common.no')}
					<div className={cn(selected === 'no' ? 'text-muted-foreground' : 'invisible')}>
						(<Kbd>Enter</Kbd> {t('confirmCard.toConfirm')})
					</div>
				</Button>
			</div>
		</div>
	);
}
