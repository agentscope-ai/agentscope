import { CodeXml } from 'lucide-react';
import { useId } from 'react';

import { Button } from '@/components/ui/button';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { Switch } from '@/components/ui/switch';
import { useTranslation } from '@/i18n/useI18n';

interface CodeModePopoverProps {
	enabled: boolean;
	disabled: boolean;
	unavailable?: boolean;
	onChange: (enabled: boolean) => Promise<void>;
}

export function CodeModePopover({
	enabled,
	disabled,
	unavailable,
	onChange,
}: CodeModePopoverProps) {
	const { t } = useTranslation();
	const id = useId();
	return (
		<Popover>
			<PopoverTrigger asChild>
				<Button
					variant={enabled ? 'secondary' : 'ghost'}
					size="sm"
					className="gap-1.5"
					aria-label={t(enabled ? 'chat.codeMode.on' : 'chat.codeMode.off')}
				>
					<CodeXml className="size-4" />
					{t('chat.codeMode.title')}
				</Button>
			</PopoverTrigger>
			<PopoverContent align="end" className="max-w-[calc(100vw-2rem)] gap-3 p-4">
				<div className="flex items-center justify-between gap-4">
					<label htmlFor={id} className="font-medium">
						{t('chat.codeMode.title')}
					</label>
					<Switch
						id={id}
						checked={enabled}
						disabled={disabled || (!enabled && unavailable)}
						onCheckedChange={(checked) => void onChange(checked)}
						aria-describedby={`${id}-description`}
					/>
				</div>
				<div id={`${id}-description`} className="space-y-2 text-xs text-muted-foreground">
					<p>{t('chat.codeMode.description')}</p>
					<p>{t('chat.codeMode.limitations')}</p>
					<p>{t('chat.codeMode.dontAsk')}</p>
					{unavailable && <p>{t('chat.codeMode.unavailable')}</p>}
				</div>
			</PopoverContent>
		</Popover>
	);
}
