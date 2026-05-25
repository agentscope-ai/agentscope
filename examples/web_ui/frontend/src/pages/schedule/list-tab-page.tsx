import { CronExpressionParser } from 'cron-parser';
import { format } from 'date-fns';
import { CalendarIcon } from 'lucide-react';
import * as React from 'react';
import { type DateRange } from 'react-day-picker';

import type { ScheduleRecord } from '@/api';
import { Button } from '@/components/ui/button';
import { Calendar } from '@/components/ui/calendar';
import { Field } from '@/components/ui/field';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { useSchedules } from '@/hooks/useSchedules';
import { useTranslation } from '@/i18n/useI18n';
import { EmptyState } from '@/pages/schedule/empty-state';
import { ScheduleCard } from '@/pages/schedule/schedule-card';
import { ScheduleDetailDrawer } from '@/pages/schedule/schedule-detail-drawer';

function scheduleHasOccurrencesInRange(
	schedule: ScheduleRecord,
	rangeStart: Date,
	rangeEnd: Date,
): boolean {
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

			return true;
		}

		return false;
	} catch {
		return true;
	}
}

export function ListTabPage() {
	const { t } = useTranslation();
	const { schedules, loading, remove } = useSchedules();
	const [selectedSchedule, setSelectedSchedule] = React.useState<ScheduleRecord | null>(null);
	const [dateRange, setDateRange] = React.useState<DateRange | undefined>(undefined);

	const filteredSchedules = React.useMemo(() => {
		if (!dateRange?.from) {
			return schedules;
		}

		const rangeStart = dateRange.from;
		const rangeEnd = dateRange.to || dateRange.from;

		return schedules.filter((schedule) =>
			scheduleHasOccurrencesInRange(schedule, rangeStart, rangeEnd),
		);
	}, [schedules, dateRange]);

	if (loading) {
		return (
			<div className="flex items-center justify-center h-full">
				<div className="text-muted-foreground">{t('common.loading')}</div>
			</div>
		);
	}

	return (
		<div className="flex flex-col size-full">
			<div className="flex flex-row w-full p-4 justify-between">
				<Field className="flex flex-row w-fit">
					<Popover>
						<PopoverTrigger asChild>
							<Button
								variant="outline"
								id="date-picker-range"
								className="justify-start px-2.5 font-normal"
								size="sm"
							>
								<CalendarIcon />
								{dateRange?.from ? (
									dateRange.to ? (
										<>
											{format(dateRange.from, 'LLL dd, y')} -{' '}
											{format(dateRange.to, 'LLL dd, y')}
										</>
									) : (
										format(dateRange.from, 'LLL dd, y')
									)
								) : (
									<span className="text-muted-foreground">
										{t('schedule.pickDate')}
									</span>
								)}
							</Button>
						</PopoverTrigger>
						<PopoverContent className="w-auto p-0" align="start">
							<Calendar
								mode="range"
								defaultMonth={dateRange?.from}
								selected={dateRange}
								onSelect={setDateRange}
								numberOfMonths={2}
							/>
						</PopoverContent>
					</Popover>
				</Field>
			</div>
			<div className="size-full overflow-y-auto p-4">
				{filteredSchedules.length === 0 ? (
					<EmptyState />
				) : (
					<div className="space-y-3">
						{filteredSchedules.map((schedule) => (
							<ScheduleCard
								key={schedule.id}
								schedule={schedule}
								onClick={() => setSelectedSchedule(schedule)}
							/>
						))}
					</div>
				)}
			</div>

			<ScheduleDetailDrawer
				schedule={selectedSchedule}
				open={!!selectedSchedule}
				onOpenChange={(open) => !open && setSelectedSchedule(null)}
				onDelete={remove}
			/>
		</div>
	);
}
