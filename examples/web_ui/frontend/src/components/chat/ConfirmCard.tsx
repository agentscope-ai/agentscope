import type { ToolCallBlock } from '@agentscope-ai/agentscope/message';
import { ChevronRight } from 'lucide-react';
import { useEffect, useState } from 'react';

import { getDisplayName, renderConfirmBody } from './tool-renderers';
import { Button } from '@/components/ui/button';
import { Kbd } from '@/components/ui/kbd';
import { useTranslation } from '@/i18n/useI18n';
import { cn } from '@/lib/utils';

type SelectOption = 'yes' | 'yes_with_rule' | 'no';

export function ConfirmCard({
	toolCall,
	onUserConfirm,
}: {
	toolCall: ToolCallBlock;
	onUserConfirm: (confirm: boolean, rules?: ToolCallBlock['suggested_rules']) => void;
}) {
	const { t } = useTranslation();
	const hasSuggestedRules = !!toolCall.suggested_rules?.length;
	const options: SelectOption[] = hasSuggestedRules
		? ['yes', 'yes_with_rule', 'no']
		: ['yes', 'no'];
	const [selected, setSelected] = useState<SelectOption>('yes');

	useEffect(() => {
		const handleKeyDown = (e: KeyboardEvent) => {
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
						onUserConfirm(true, [toolCall.suggested_rules![0]]);
					} else {
						onUserConfirm(selected === 'yes');
					}
					break;
			}
		};

		window.addEventListener('keydown', handleKeyDown);
		return () => window.removeEventListener('keydown', handleKeyDown);
	}, [onUserConfirm, selected, options]);

	return (
		<div className="ring ring-border rounded-xl w-full p-4 space-y-4 text-sm">
			<div className="flex flex-col gap-y-2">
				<strong className="text-secondary-foreground">{getDisplayName(toolCall, t)}</strong>
				<div className="px-4 py-2 bg-white rounded-sm">
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
					onClick={(e) => {
						e.stopPropagation();
						e.preventDefault();
						onUserConfirm(true);
					}}
				>
					<ChevronRight
						className={cn('size-4', selected === 'yes' ? 'visible' : 'invisible')}
					/>
					1. {t('common.yes')}
					<div className={cn(selected === 'yes' ? 'text-muted-foreground' : 'invisible')}>
						(<Kbd>Enter</Kbd> {t('confirmCard.toConfirm')})
					</div>
				</Button>
				{hasSuggestedRules && (
					<Button
						className={cn(
							'flex justify-start cursor-pointer',
							selected === 'yes_with_rule' ? 'text-primary' : 'text-muted-foreground',
						)}
						size="sm"
						variant="ghost"
						onMouseEnter={() => setSelected('yes_with_rule')}
						onClick={(e) => {
							e.stopPropagation();
							e.preventDefault();
							onUserConfirm(true, [toolCall.suggested_rules![0]]);
						}}
					>
						<ChevronRight
							className={cn(
								'size-4',
								selected === 'yes_with_rule' ? 'visible' : 'invisible',
							)}
						/>
						2.{' '}
						{t('confirmCard.yesWithRule', {
							toolName: toolCall.suggested_rules![0].tool_name,
							ruleContent: toolCall.suggested_rules![0].rule_content,
						})}
						<div
							className={cn(
								selected === 'yes_with_rule'
									? 'text-muted-foreground'
									: 'invisible',
							)}
						>
							(<Kbd>Enter</Kbd> {t('confirmCard.toConfirm')})
						</div>
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
					onClick={(e) => {
						e.stopPropagation();
						e.preventDefault();
						onUserConfirm(false);
					}}
				>
					<ChevronRight
						className={cn('size-4', selected === 'no' ? 'visible' : 'invisible')}
					/>
					{hasSuggestedRules ? '3' : '2'}. {t('common.no')}
					<div className={cn(selected === 'no' ? 'text-muted-foreground' : 'invisible')}>
						(<Kbd>Enter</Kbd> {t('confirmCard.toConfirm')})
					</div>
				</Button>
			</div>
		</div>
	);
}
