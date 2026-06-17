import { Trash2 } from 'lucide-react';
import * as React from 'react';
import { useNavigate } from 'react-router-dom';

import { parseCronExpression, getFrequencyLabel } from './schedule-utils';
import type { ScheduleRecord, SessionRecord } from '@/api';
import { scheduleApi } from '@/api';
import { StatusBadge } from '@/components/badge/StatusBadge';
import { DeleteDialog } from '@/components/dialog/DeleteDialog';
import { Button } from '@/components/ui/button';
import {
	Drawer,
	DrawerContent,
	DrawerDescription,
	DrawerFooter,
	DrawerHeader,
	DrawerTitle,
} from '@/components/ui/drawer';
import { Separator } from '@/components/ui/separator';
import { useAgents } from '@/hooks/useAgents';
import { useTranslation } from '@/i18n/useI18n';

interface ScheduleDetailDrawerProps {
	schedule: ScheduleRecord | null;
	open: boolean;
	onOpenChange: (open: boolean) => void;
	onDelete: (scheduleId: string) => Promise<void>;
}

export function ScheduleDetailDrawer({
	schedule,
	open,
	onOpenChange,
	onDelete,
}: ScheduleDetailDrawerProps) {
	const { t } = useTranslation();
	const { agents } = useAgents();
	const navigate = useNavigate();
	const [openDeleteDialog, setOpenDeleteDialog] = React.useState(false);
	const [sessions, setSessions] = React.useState<SessionRecord[]>([]);
	const [sessionsLoading, setSessionsLoading] = React.useState(false);

	React.useEffect(() => {
		if (!schedule || !open) {
			setSessions([]);
			return;
		}
		setSessionsLoading(true);
		scheduleApi
			.listSessions(schedule.id)
			.then((res) => setSessions(res.sessions))
			.catch(() => setSessions([]))
			.finally(() => setSessionsLoading(false));
	}, [schedule, open]);

	const handleDelete = async () => {
		if (!schedule) return;
		await onDelete(schedule.id);
		onOpenChange(false);
	};

	if (!schedule) return null;

	const { data } = schedule;
	const parsed = parseCronExpression(data.cron_expression, data.started_at);
	const agentName =
		agents.find((a) => a.id === schedule.agent_id)?.data.name ?? schedule.agent_id;

	const weekdayNames = [
		t('schedule.sunday'),
		t('schedule.monday'),
		t('schedule.tuesday'),
		t('schedule.wednesday'),
		t('schedule.thursday'),
		t('schedule.friday'),
		t('schedule.saturday'),
	];

	let triggerTimeDisplay = parsed.time;
	switch (parsed.frequency) {
		case 'weekly':
			triggerTimeDisplay = `${weekdayNames[parsed.weekday ?? 0]} ${parsed.time}`;
			break;
		case 'monthly':
			triggerTimeDisplay = `${parsed.dayOfMonth ?? 1}${t('schedule.dayOfMonthSuffix')} ${parsed.time}`;
			break;
		case 'once':
			triggerTimeDisplay = parsed.date
				? `${parsed.date.toLocaleDateString()} ${parsed.time}`
				: parsed.time;
			break;
	}
	triggerTimeDisplay += ` (${data.timezone})`;

	const scheduleInfoItems = [
		{
			title: t('schedule.frequency'),
			content: getFrequencyLabel(parsed, t),
		},
		{
			title: t('schedule.triggerTime'),
			content: triggerTimeDisplay,
		},
		{
			title: t('schedule.createdAt'),
			content: new Date(schedule.created_at).toLocaleString(),
		},
		{
			title: t('schedule.end_at'),
			content: data.ended_at ? new Date(data.ended_at).toLocaleString() : t('common.noData'),
		},
		{
			title: t('common.agent'),
			content: agentName,
		},
		{
			title: t('schedule.permissionMode'),
			content: data.permission_mode,
		},
		{
			title: t('schedule.stateful'),
			content: data.stateful ? t('common.yes') : t('common.no'),
		},
	];

	return (
		<>
			<Drawer open={open} onOpenChange={onOpenChange} direction="right">
				<DrawerContent className="max-h-full h-full min-w-[30rem] ml-auto border-none ring-none! shadow-none!">
					<DrawerHeader>
						<DrawerTitle>{data.name}</DrawerTitle>
						<DrawerDescription>{data.description}</DrawerDescription>
					</DrawerHeader>
					<div className="flex flex-1 flex-col overflow-hidden gap-y-4 p-4">
						<div className="flex flex-col space-y-2">
							<h3 className="text-sm font-semibold text-secondary-foreground">
								{t('common.information')}
							</h3>
							<div className="flex flex-col gap-2">
								{scheduleInfoItems.map((item) => (
									<div
										key={item.title}
										className="flex flex-row justify-between text-xs px-2.5 py-2 font-mono rounded-md ring ring-border items-center"
									>
										<span className="font-medium">
											{item.title.toUpperCase()}
										</span>
										<span className="ml-auto text-muted-foreground">
											{item.content}
										</span>
									</div>
								))}
							</div>
						</div>
						<Separator />
						<div className="space-y-2 flex flex-col flex-1 overflow-hidden">
							<h3 className="text-sm font-semibold">
								{t('schedule.executionHistory')}
							</h3>
							<div className="flex-1 overflow-y-auto space-y-1">
								{sessionsLoading && (
									<div className="text-center py-4 text-muted-foreground text-sm">
										{t('common.loading')}
									</div>
								)}
								{!sessionsLoading && sessions.length === 0 && (
									<div className="text-center py-4 text-muted-foreground text-sm">
										{t('common.noData')}
									</div>
								)}
								{!sessionsLoading &&
									sessions.map((session) => (
										<div
											key={session.id}
											className="flex flex-row justify-between text-xs px-2.5 py-2 font-mono rounded-md ring ring-border items-center cursor-pointer hover:bg-muted/50 transition-colors"
											onClick={() => {
												navigate(
													`/chat/${schedule.agent_id}/${session.id}`,
												);
												onOpenChange(false);
											}}
										>
											<span className="text-muted-foreground">
												{new Date(session.created_at).toLocaleString()}
											</span>
											<StatusBadge status="completed" />
										</div>
									))}
							</div>
						</div>
					</div>
					<DrawerFooter>
						<Button
							variant="destructive"
							size="sm"
							onClick={(e) => {
								e.preventDefault();
								setOpenDeleteDialog(true);
							}}
						>
							<Trash2 className="size-3" />
							{t('common.delete')}
						</Button>
					</DrawerFooter>
				</DrawerContent>
			</Drawer>

			<DeleteDialog
				open={openDeleteDialog}
				onOpenChange={setOpenDeleteDialog}
				title={t('common.deleteTitle', {
					entity: t('schedule.deleteSchedule.entity'),
					name: data.name,
				})}
				description={t('common.deleteDescription')}
				onConfirm={handleDelete}
			/>
		</>
	);
}
