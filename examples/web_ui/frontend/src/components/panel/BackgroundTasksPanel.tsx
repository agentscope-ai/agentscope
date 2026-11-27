import { formatDistanceStrict } from 'date-fns';
import { enUS, zhCN } from 'date-fns/locale';
import {
	Activity,
	Check,
	ChevronDown,
	CircleAlert,
	CircleCheck,
	CircleSlash,
	Copy,
	Loader2,
	MessageSquare,
} from 'lucide-react';
import { useEffect, useState } from 'react';

import type { BackgroundTaskInfo } from '@/api';
import { PanelEmpty } from '@/components/panel/PanelEmpty';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { useTranslation } from '@/i18n/useI18n';
import { cn } from '@/lib/utils';
import { copyToClipboard } from '@/utils/common';

const STATUS_ICONS = {
	running: Loader2,
	completed: CircleCheck,
	failed: CircleAlert,
	cancelled: CircleSlash,
};

function BackgroundTaskRow({ task, now }: { task: BackgroundTaskInfo; now: number }) {
	const { t, i18n } = useTranslation();
	const [copied, setCopied] = useState(false);
	const Icon = STATUS_ICONS[task.status];
	const started = new Date(task.started_at).getTime();
	const ended = task.completed_at ? new Date(task.completed_at).getTime() : now;
	const duration = formatDistanceStrict(started, Math.max(started, ended), {
		locale: i18n.language.startsWith('zh') ? zhCN : enUS,
	});
	const executionStarted = task.execution_started_at
		? Date.parse(task.execution_started_at)
		: null;
	const totalDuration =
		executionStarted !== null
			? formatDistanceStrict(executionStarted, Math.max(executionStarted, ended), {
					locale: i18n.language.startsWith('zh') ? zhCN : enUS,
				})
			: null;

	useEffect(() => {
		if (!copied) return;
		const timer = window.setTimeout(() => setCopied(false), 2000);
		return () => window.clearTimeout(timer);
	}, [copied]);

	return (
		<li className="min-w-0 rounded-xl border border-border/60 p-3">
			<div className="flex items-start justify-between gap-2">
				<span className="min-w-0 break-words text-sm font-medium [overflow-wrap:anywhere]">
					{task.tool_name}
				</span>
				<Badge
					variant="outline"
					className={cn('shrink-0 gap-1', task.status === 'failed' && 'text-destructive')}
				>
					<Icon
						className={cn(
							'size-3',
							task.status === 'running' && 'motion-safe:animate-spin',
						)}
					/>
					{t(`panel.background.status.${task.status}`)}
				</Badge>
			</div>
			{task.summary && (
				<p className="mt-2 whitespace-pre-wrap text-xs leading-relaxed text-muted-foreground [overflow-wrap:anywhere]">
					{task.summary}
				</p>
			)}
			<p className="mt-2 text-xs tabular-nums text-muted-foreground">
				{totalDuration !== null
					? t('panel.background.totalDuration', { duration: totalDuration })
					: t('panel.background.duration', { duration })}
			</p>
			{totalDuration !== null && (
				<p className="mt-1 text-xs tabular-nums text-muted-foreground">
					{t('panel.background.backgroundDuration', { duration })}
				</p>
			)}
			<div className="mt-1 flex min-w-0 items-center gap-1">
				<span className="shrink-0 text-xs text-muted-foreground">ID</span>
				<code
					className="min-w-0 truncate text-xs text-muted-foreground"
					title={task.task_id}
				>
					{task.task_id}
				</code>
				<Button
					variant="ghost"
					size="icon-xs"
					aria-label={t(copied ? 'panel.background.copied' : 'panel.background.copyId')}
					tooltip={t(copied ? 'panel.background.copied' : 'panel.background.copyId')}
					onClick={async () => {
						if (await copyToClipboard(task.task_id)) setCopied(true);
					}}
				>
					{copied ? <Check /> : <Copy />}
				</Button>
			</div>
			{task.status === 'failed' && task.error_summary && (
				<Collapsible className="mt-2 border-t border-border/60 pt-2">
					<CollapsibleTrigger asChild>
						<Button variant="ghost" size="xs" className="group px-0 text-destructive">
							<ChevronDown className="transition-transform group-data-[state=open]:rotate-180" />
							{t('panel.background.errorDetails')}
						</Button>
					</CollapsibleTrigger>
					<CollapsibleContent>
						<p className="mt-2 whitespace-pre-wrap text-xs leading-relaxed text-muted-foreground [overflow-wrap:anywhere]">
							{task.error_summary}
						</p>
					</CollapsibleContent>
				</Collapsible>
			)}
		</li>
	);
}

interface BackgroundTasksPanelProps {
	sessionId: string | null;
	tasks: BackgroundTaskInfo[] | undefined;
	loading: boolean;
	error: boolean;
	refreshing: boolean;
	visible: boolean;
	updatedAt: number;
	onRefresh: () => void;
}

/** Read-only view of running tools and the server's retained terminal snapshots. */
export function BackgroundTasksPanel({
	sessionId,
	tasks,
	loading,
	error,
	refreshing,
	visible,
	updatedAt,
	onRefresh,
}: BackgroundTasksPanelProps) {
	const { t } = useTranslation();
	const [now, setNow] = useState(Date.now);
	const running = tasks?.filter((task) => task.status === 'running') ?? [];
	const recent =
		tasks
			?.filter((task) => task.status !== 'running')
			.sort((a, b) => Date.parse(b.completed_at!) - Date.parse(a.completed_at!)) ?? [];
	const ticking = visible && !error && running.length > 0;

	useEffect(() => {
		if (!ticking) return;
		setNow(Date.now());
		const timer = window.setInterval(() => setNow(Date.now()), 1000);
		return () => window.clearInterval(timer);
	}, [ticking]);

	if (!sessionId) {
		return <PanelEmpty icon={MessageSquare} title={t('panel.background.noSession')} />;
	}
	if (loading) {
		return (
			<div
				role="status"
				className="flex flex-1 items-center justify-center gap-2 p-4 text-sm text-muted-foreground"
			>
				<Loader2 className="size-4 motion-safe:animate-spin" />
				{t('panel.loading')}
			</div>
		);
	}

	return (
		<div className="flex min-w-0 flex-1 flex-col gap-3 py-3">
			{error && (
				<div
					role="status"
					className="rounded-xl border border-destructive/20 bg-destructive/5 p-3"
				>
					<p className="text-xs leading-relaxed">
						{t(tasks ? 'panel.background.stale' : 'panel.background.loadError')}
					</p>
					<Button
						variant="outline"
						size="xs"
						className="mt-2"
						disabled={refreshing}
						onClick={onRefresh}
					>
						{t('common.retry')}
					</Button>
				</div>
			)}
			{tasks && tasks.length === 0 && !error && (
				<PanelEmpty
					icon={Activity}
					title={t('panel.background.emptyTitle')}
					description={t('panel.background.emptyDescription')}
				/>
			)}
			{tasks && tasks.length > 0 && (
				<>
					{running.length > 0 ? (
						<ul className="space-y-2" aria-label={t('panel.background.status.running')}>
							{running.map((task) => (
								<BackgroundTaskRow
									key={task.task_id}
									task={task}
									now={error ? updatedAt : now}
								/>
							))}
						</ul>
					) : (
						<p className="text-xs text-muted-foreground">
							{t('panel.background.noRunning')}
						</p>
					)}
					{recent.length > 0 && (
						<Collapsible className="min-w-0 border-t border-border/60 pt-2">
							<CollapsibleTrigger asChild>
								<Button
									variant="ghost"
									size="sm"
									className="group w-full justify-between px-0"
								>
									{t('panel.background.recent', { count: recent.length })}
									<ChevronDown className="transition-transform group-data-[state=open]:rotate-180" />
								</Button>
							</CollapsibleTrigger>
							<CollapsibleContent>
								<p className="mb-3 mt-1 text-xs leading-relaxed text-muted-foreground">
									{t('panel.background.retention')}
								</p>
								<ul className="space-y-2">
									{recent.map((task) => (
										<BackgroundTaskRow
											key={task.task_id}
											task={task}
											now={now}
										/>
									))}
								</ul>
							</CollapsibleContent>
						</Collapsible>
					)}
				</>
			)}
		</div>
	);
}
