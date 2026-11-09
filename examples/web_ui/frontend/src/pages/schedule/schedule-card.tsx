import { Pause, Calendar, ArrowRight, Bot, ClipboardClock, BotOff } from 'lucide-react';

import { parseCronExpression, getFrequencyLabel } from './schedule-utils';
import type { ScheduleRecord } from '@/api';
import { Badge } from '@/components/ui/badge';
import { Item, ItemContent, ItemDescription, ItemMedia, ItemTitle } from '@/components/ui/item';
import { useTranslation } from '@/i18n/useI18n';

interface ScheduleCardProps {
	schedule: ScheduleRecord;
	onClick: () => void;
}

export function ScheduleCard({ schedule, onClick }: ScheduleCardProps) {
	const { t } = useTranslation();
	const { data } = schedule;
	const parsed = parseCronExpression(data.cron_expression, data.started_at);

	return (
		<Item className="cursor-pointer hover:shadow-md" variant="outline" onClick={onClick}>
			<ItemMedia variant="icon">{data.enabled ? <Bot /> : <BotOff />}</ItemMedia>
			<ItemContent>
				<ItemTitle className="font-[550] truncate max-w-full">{data.name}</ItemTitle>
				<ItemDescription className="flex gap-x-2 items-center text-xs truncate overflow-hidden">
					{!data.enabled && (
						<Badge variant="secondary" className="gap-1">
							<Pause className="h-3 w-3" />
							{t('common.disabled')}
						</Badge>
					)}
					<Badge variant="link" className="pl-0">
						<Calendar data-icon="inline-start" />
						{new Date(data.started_at).toLocaleDateString()}
						{data.ended_at && (
							<div className="flex items-center gap-1">
								<ArrowRight className="size-3 text-muted-foreground" />
								{new Date(data.ended_at).toLocaleDateString()}
							</div>
						)}
						<div>{parsed.time}</div>
					</Badge>
					<Badge variant="link">
						<ClipboardClock data-icon="inline-start" />
						{getFrequencyLabel(parsed, t)}
					</Badge>
				</ItemDescription>
			</ItemContent>
		</Item>
	);
}
