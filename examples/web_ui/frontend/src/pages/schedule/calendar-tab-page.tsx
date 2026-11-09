import { ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';

import type { ScheduleEvent } from './event';
import { Button } from '@/components/ui/button';
import {
	DropdownMenu,
	DropdownMenuContent,
	DropdownMenuItem,
	DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { useTranslation } from '@/i18n/useI18n';

interface DateCellProps {
	day: number;
	isToday: boolean;
	events: ScheduleEvent[];
	onEventClick: (event: ScheduleEvent) => void;
	moreText: string;
}

/**
 * DateCell component that dynamically adjusts the number of visible events based on cell height.
 * @param root0
 * @param root0.day
 * @param root0.isToday
 * @param root0.events
 * @param root0.onEventClick
 * @param root0.moreText
 * @returns A calendar date cell element.
 */
function DateCell({ day, isToday, events, onEventClick, moreText }: DateCellProps) {
	const cellRef = useRef<HTMLDivElement>(null);
	const [maxVisibleEvents, setMaxVisibleEvents] = useState(3);

	useEffect(() => {
		if (!cellRef.current) return;

		const resizeObserver = new ResizeObserver((entries) => {
			for (const entry of entries) {
				const cellHeight = entry.contentRect.height;
				// Calculate available height for events
				// Cell padding: 8px (p-2)
				// Date header: ~28px (w-6 h-6 + mb-1)
				// Event item height: ~24px (text-xs + py-0.5 + space-y-1)
				// More indicator: ~20px
				const padding = 8;
				const headerHeight = 28;
				const eventHeight = 24;
				const moreIndicatorHeight = 20;

				const availableHeight = cellHeight - padding - headerHeight - moreIndicatorHeight;
				const maxEvents = Math.max(0, Math.floor(availableHeight / eventHeight));
				setMaxVisibleEvents(maxEvents);
			}
		});

		resizeObserver.observe(cellRef.current);

		return () => {
			resizeObserver.disconnect();
		};
	}, []);

	return (
		<div
			ref={cellRef}
			className="border-border! border-r border-b p-2 hover:bg-accent cursor-pointer overflow-hidden"
		>
			<div className="flex items-start justify-between mb-1">
				<div className="w-6 h-6 flex items-center justify-center">
					<div
						className={`text-sm ${
							isToday
								? 'bg-primary text-primary-foreground rounded-full w-6 h-6 flex items-center justify-center'
								: ''
						}`}
					>
						{day}
					</div>
				</div>
			</div>
			{/* Event indicators */}
			<div className="space-y-1">
				{events.slice(0, maxVisibleEvents).map((event) => (
					<div
						key={event.id}
						className="flex flex-row justify-between text-xs py-0.5 px-1 text-secondary-foreground rounded-sm cursor-pointer hover:bg-primary/20"
						title={`${event.time} - ${event.title}`}
						onClick={(e) => {
							e.stopPropagation();
							onEventClick(event);
						}}
					>
						<div className="flex flex-row flex-1 items-center gap-x-1 overflow-hidden">
							<div className="w-1 min-w-1 max-w-1 bg-primary border-primary h-full rounded" />
							<span className="truncate">{event.title}</span>
						</div>
						<span>{event.time}</span>
					</div>
				))}
				{events.length > maxVisibleEvents && (
					<DropdownMenu>
						<DropdownMenuTrigger asChild>
							<div className="text-xs text-muted-foreground px-1 cursor-pointer hover:text-foreground hover:underline">
								+{events.length - maxVisibleEvents} {moreText}
							</div>
						</DropdownMenuTrigger>
						<DropdownMenuContent align="start" className="max-w-64">
							{events.slice(maxVisibleEvents).map((event) => (
								<DropdownMenuItem
									key={event.id}
									onClick={() => onEventClick(event)}
									className="text-xs"
								>
									<span className="font-medium">{event.time}</span>
									<span className="truncate ml-2">{event.title}</span>
								</DropdownMenuItem>
							))}
						</DropdownMenuContent>
					</DropdownMenu>
				)}
			</div>
		</div>
	);
}

interface Props {
	events: ScheduleEvent[];
	onEventClick: (event: ScheduleEvent) => void;
	currentDate: Date;
	onMonthChange: (date: Date) => void;
}

/**
 * The calendar tab page component that displays events in a monthly calendar view.
 *
 * @param root0 - The component props.
 * @param root0.events - Array of schedule events to display.
 * @param root0.onEventClick - Callback when an event is clicked.
 * @param root0.currentDate - The currently displayed date.
 * @param root0.onMonthChange - Callback when the month is changed.
 * @returns A CalendarTabPage component.
 */
export function CalendarTabPage({ events, onEventClick, currentDate, onMonthChange }: Props) {
	const { t } = useTranslation();
	const goToPrevMonth = () => {
		onMonthChange(new Date(year, month - 1, 1));
	};

	const goToNextMonth = () => {
		onMonthChange(new Date(year, month + 1, 1));
	};

	const goToPrevYear = () => {
		onMonthChange(new Date(year - 1, month, 1));
	};

	const goToNextYear = () => {
		onMonthChange(new Date(year + 1, month, 1));
	};

	const goToToday = () => {
		onMonthChange(new Date());
	};

	const year = currentDate.getFullYear();
	const month = currentDate.getMonth();

	// Get the first and last day of the current month
	const firstDay = new Date(year, month, 1);
	const lastDay = new Date(year, month + 1, 0);

	// Get the day of the week for the first day of the month (0-6, 0 is Sunday)
	const firstDayOfWeek = firstDay.getDay();

	// Get the last few days of the previous month
	const prevMonthLastDay = new Date(year, month, 0).getDate();
	const prevMonthDays = Array.from(
		{ length: firstDayOfWeek },
		(_, i) => prevMonthLastDay - firstDayOfWeek + i + 1,
	);

	// Get all days of the current month
	const currentMonthDays = Array.from({ length: lastDay.getDate() }, (_, i) => i + 1);

	// Calculate the actual number of weeks needed for this month
	const totalDaysNeeded = prevMonthDays.length + currentMonthDays.length;
	const weeksNeeded = Math.ceil(totalDaysNeeded / 7);
	const totalCells = weeksNeeded * 7;
	const remainingCells = totalCells - prevMonthDays.length - currentMonthDays.length;
	const nextMonthDays = Array.from({ length: remainingCells }, (_, i) => i + 1);

	const weekDays = [
		t('schedule.sunday'),
		t('schedule.monday'),
		t('schedule.tuesday'),
		t('schedule.wednesday'),
		t('schedule.thursday'),
		t('schedule.friday'),
		t('schedule.saturday'),
	];

	// Get events for a specific date
	const getEventsForDate = (date: Date) => {
		const y = date.getFullYear();
		const m = String(date.getMonth() + 1).padStart(2, '0');
		const d = String(date.getDate()).padStart(2, '0');
		const dateStr = `${y}-${m}-${d}`;
		return events.filter((event) => event.date === dateStr);
	};

	return (
		<div className="flex flex-col size-full">
			<div className="flex items-center justify-between p-4">
				<h2 className="text-xl font-semibold">
					{new Date(year, month).toLocaleDateString('en-US', {
						year: 'numeric',
						month: 'long',
					})}
				</h2>
				<div className="flex items-center gap-2">
					<Button variant="outline" size="sm" onClick={goToToday}>
						{t('schedule.today')}
					</Button>
					<TooltipProvider>
						<Tooltip>
							<TooltipTrigger asChild>
								<Button variant="outline" size="icon-sm" onClick={goToPrevYear}>
									<ChevronsLeft className="h-4 w-4" />
								</Button>
							</TooltipTrigger>
							<TooltipContent>
								<p>{t('schedule.previousYear')}</p>
							</TooltipContent>
						</Tooltip>
					</TooltipProvider>
					<TooltipProvider>
						<Tooltip>
							<TooltipTrigger asChild>
								<Button variant="outline" size="icon-sm" onClick={goToPrevMonth}>
									<ChevronLeft className="h-4 w-4" />
								</Button>
							</TooltipTrigger>
							<TooltipContent>
								<p>{t('schedule.previousMonth')}</p>
							</TooltipContent>
						</Tooltip>
					</TooltipProvider>
					<TooltipProvider>
						<Tooltip>
							<TooltipTrigger asChild>
								<Button variant="outline" size="icon-sm" onClick={goToNextMonth}>
									<ChevronRight className="h-4 w-4" />
								</Button>
							</TooltipTrigger>
							<TooltipContent>
								<p>{t('schedule.nextMonth')}</p>
							</TooltipContent>
						</Tooltip>
					</TooltipProvider>
					<TooltipProvider>
						<Tooltip>
							<TooltipTrigger asChild>
								<Button variant="outline" size="icon-sm" onClick={goToNextYear}>
									<ChevronsRight className="h-4 w-4" />
								</Button>
							</TooltipTrigger>
							<TooltipContent>
								<p>{t('schedule.nextYear')}</p>
							</TooltipContent>
						</Tooltip>
					</TooltipProvider>
				</div>
			</div>

			<div className="flex-1 flex flex-col">
				{/* Week day headers */}
				<div className="grid grid-cols-7 border-b border-border!">
					{weekDays.map((day) => (
						<div
							key={day}
							className="text-center py-2 text-sm font-medium text-muted-foreground"
						>
							{day}
						</div>
					))}
				</div>

				{/* Date grid */}
				<div
					className={`flex-1 grid grid-cols-7`}
					style={{ gridTemplateRows: `repeat(${weeksNeeded}, minmax(0, 1fr))` }}
				>
					{/* Previous month dates */}
					{prevMonthDays.map((day, index) => (
						<div
							key={`prev-${index}`}
							className="border-r border-b border-border! p-2 text-muted-foreground/50"
						>
							<div className="text-sm">{day}</div>
						</div>
					))}

					{/* Current month dates */}
					{currentMonthDays.map((day) => {
						const date = new Date(year, month, day);
						const isToday =
							date.getDate() === new Date().getDate() &&
							date.getMonth() === new Date().getMonth() &&
							date.getFullYear() === new Date().getFullYear();
						const dayEvents = getEventsForDate(date);

						return (
							<DateCell
								key={`current-${day}`}
								day={day}
								isToday={isToday}
								events={dayEvents}
								onEventClick={onEventClick}
								moreText={t('schedule.more')}
							/>
						);
					})}

					{/* Next month dates */}
					{nextMonthDays.map((day, index) => (
						<div
							key={`next-${index}`}
							className="border-border! border-r border-b p-2 text-muted-foreground/50"
						>
							<div className="text-sm">{day}</div>
						</div>
					))}
				</div>
			</div>
		</div>
	);
}
