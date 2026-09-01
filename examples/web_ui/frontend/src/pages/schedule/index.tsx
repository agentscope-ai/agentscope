import { CronExpressionParser } from 'cron-parser';
import { Calendar, List, Plus } from 'lucide-react';
import * as React from 'react';

import type { ScheduleEvent } from './event';
import type { ScheduleRecord } from '@/api';
import { Button } from '@/components/ui/button';
import { Separator } from '@/components/ui/separator';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { useSchedules } from '@/hooks/useSchedules';
import { useTranslation } from '@/i18n/useI18n';
import { CalendarTabPage } from '@/pages/schedule/calendar-tab-page';
import { CreateScheduleDialog } from '@/pages/schedule/create-schedule-dialog';
import { ListTabPage } from '@/pages/schedule/list-tab-page';
import { ScheduleDetailDrawer } from '@/pages/schedule/schedule-detail-drawer';

function expandScheduleToEvents(
	schedule: ScheduleRecord,
	rangeStart: Date,
	rangeEnd: Date,
): ScheduleEvent[] {
	const events: ScheduleEvent[] = [];
	const { data } = schedule;
	try {
		const interval = CronExpressionParser.parse(data.cron_expression, {
			currentDate: rangeStart,
			endDate: rangeEnd,
		});
		const startTs = new Date(data.started_at).getTime();
		const endTs = data.ended_at ? new Date(data.ended_at).getTime() : null;
		while (interval.hasNext()) {
			const date = interval.next().toDate();
			const ts = date.getTime();
			if (ts < startTs) continue;
			if (endTs && ts > endTs) break;
			const localDate = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
			const localTime = `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`;
			events.push({
				id: `${schedule.id}:${ts}`,
				title: data.name,
				date: localDate,
				time: localTime,
				content: data.description,
			});
		}
	} catch {
		// invalid cron expr — skip
	}
	return events;
}

export function SchedulePage() {
	const { t } = useTranslation();
	const { schedules, remove, refetch } = useSchedules();
	const [viewMode, setViewMode] = React.useState<'calendar' | 'list'>('calendar');
	const [currentDate, setCurrentDate] = React.useState(new Date());
	const [selectedSchedule, setSelectedSchedule] = React.useState<ScheduleRecord | null>(null);
	const [isCreateOpen, setIsCreateOpen] = React.useState(false);

	const rangeStart = React.useMemo(
		() => new Date(currentDate.getFullYear(), currentDate.getMonth(), 1),
		[currentDate],
	);
	const rangeEnd = React.useMemo(
		() => new Date(currentDate.getFullYear(), currentDate.getMonth() + 1, 0, 23, 59, 59),
		[currentDate],
	);

	const events = React.useMemo(() => {
		return schedules
			.filter((s) => s.data.enabled)
			.flatMap((s) => expandScheduleToEvents(s, rangeStart, rangeEnd));
	}, [schedules, rangeStart, rangeEnd]);

	const handleEventClick = (event: ScheduleEvent) => {
		const scheduleId = event.id.split(':')[0];
		const schedule = schedules.find((s) => s.id === scheduleId);
		if (schedule) setSelectedSchedule(schedule);
	};

	return (
		<div className="flex size-full p-2">
			<main className="flex h-full min-h-0 min-w-0 flex-1 flex-col overflow-hidden rounded-[22px] bg-card shadow-panel">
				<div className="flex items-start justify-between gap-3 px-6 pt-5 pb-4">
					<div>
						<div className="text-2xl font-semibold">{t('common.schedule')}</div>
						<div className="mt-1 text-sm text-muted-foreground">
							{t('schedule.subtitle')}
						</div>
					</div>
					<div className="flex items-center gap-2">
						<Button size="icon-sm" onClick={() => setIsCreateOpen(true)}>
							<Plus />
						</Button>
						<Tabs
							value={viewMode}
							onValueChange={(value) => setViewMode(value as 'calendar' | 'list')}
						>
							<TabsList>
								<TabsTrigger value="calendar" className="border-none w-[100px]">
									<Calendar className="h-4 w-4" />
									<span>{t('schedule.calendar')}</span>
								</TabsTrigger>
								<TabsTrigger value="list" className="border-none w-[100px]">
									<List className="h-4 w-4" />
									<span>{t('schedule.list')}</span>
								</TabsTrigger>
							</TabsList>
						</Tabs>
					</div>
				</div>
				<Separator />

				<div className="flex-1 overflow-hidden">
					{viewMode === 'calendar' && (
						<CalendarTabPage
							events={events}
							onEventClick={handleEventClick}
							currentDate={currentDate}
							onMonthChange={setCurrentDate}
						/>
					)}
					{viewMode === 'list' && <ListTabPage />}
				</div>
			</main>

			<ScheduleDetailDrawer
				schedule={selectedSchedule}
				open={!!selectedSchedule}
				onOpenChange={(open) => !open && setSelectedSchedule(null)}
				onDelete={remove}
			/>

			<CreateScheduleDialog
				open={isCreateOpen}
				onOpenChange={setIsCreateOpen}
				onCreated={refetch}
			/>
		</div>
	);
}
