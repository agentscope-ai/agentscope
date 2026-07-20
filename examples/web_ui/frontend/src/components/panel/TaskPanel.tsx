import type { Task, TaskContext } from '@agentscope-ai/agentscope/state';
import { Ellipsis, ListX, Loader2, Square, SquareCheck } from 'lucide-react';
import { useState } from 'react';

import { PanelEmpty } from '@/components/panel/PanelEmpty';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { useTranslation } from '@/i18n/useI18n';
import { cn } from '@/lib/utils';

interface TaskPanelProps {
	/**
	 * The task context to render. Pass ``null`` when no data is
	 * available yet (renders nothing).
	 */
	tasksContext: TaskContext | null;
	className?: string;
}

/**
 * State icon for a single task row.
 *
 * @param state - The task's current state.
 * @returns An icon element sized for inline display.
 */
function StateIcon({ state }: { state: Task['state'] }) {
	switch (state) {
		case 'completed':
			return <SquareCheck className="size-3 shrink-0" />;
		case 'in_progress':
			return <Loader2 className="size-3 animate-spin shrink-0" />;
		default:
			return <Square className="size-3 shrink-0" />;
	}
}

/**
 * Filters the task list so that only the last 3 of any leading run of
 * completed tasks are shown; earlier ones are replaced by a single
 * ellipsis sentinel.
 *
 * @param tasks - The full ordered task array.
 * @returns An object with the visible task slice and a boolean flag
 *   indicating whether the ellipsis row should be rendered.
 */
function filterTasksWithEllipsis(tasks: Task[]): {
	showEllipsis: boolean;
	visibleTasks: Task[];
} {
	// Count how many consecutive completed tasks appear at the front.
	let consecutiveCompleted = 0;
	for (const task of tasks) {
		if (task.state === 'completed') {
			consecutiveCompleted++;
		} else {
			break;
		}
	}

	const MAX_VISIBLE_COMPLETED = 3;
	if (consecutiveCompleted <= MAX_VISIBLE_COMPLETED) {
		return { showEllipsis: false, visibleTasks: tasks };
	}

	// Discard all but the last MAX_VISIBLE_COMPLETED completed tasks.
	return {
		showEllipsis: true,
		visibleTasks: tasks.slice(consecutiveCompleted - MAX_VISIBLE_COMPLETED),
	};
}

/**
 * Compact, read-only panel listing the agent's current tasks with
 * their status and dependency information.
 *
 * Each row shows ``#id  [icon]  subject  [← blocked by #x, #y]``.
 * The panel header displays a progress summary like ``Tasks (3/5)``.
 * When there are more than 3 leading completed tasks, the earlier ones
 * are collapsed into a single ``…`` separator row.
 *
 * @param tasksContext - The full ``TaskContext`` from ``AgentState``.
 *   ``null`` hides the panel entirely.
 * @param className - The className
 * @returns A panel element, or ``null`` when there are no tasks.
 */
export function TaskPanel({ tasksContext, className }: TaskPanelProps) {
	const { t } = useTranslation();
	const [expanded, setExpanded] = useState(false);

	if (!tasksContext || tasksContext.tasks.length === 0) {
		return (
			<PanelEmpty
				icon={ListX}
				title={t('panel.plan.emptyTitle')}
				description={t('panel.plan.emptyDescription')}
			/>
		);
	}

	const { tasks } = tasksContext;
	const completed = tasks.filter((task) => task.state === 'completed').length;
	const { showEllipsis, visibleTasks } = filterTasksWithEllipsis(tasks);

	const displayedTasks = expanded ? tasks : visibleTasks;

	return (
		<div className={cn('flex flex-col flex-1 min-h-0 text-sm', className)}>
			<div className="flex flex-row items-center gap-x-2 shrink-0 pb-2">
				<Badge variant={'secondary'} className="tracking-wide">
					{t('panel.plan.completed', { count: completed })}
				</Badge>
				<Badge variant={'secondary'} className="tracking-wide">
					{t('panel.plan.total', { count: tasks.length })}
				</Badge>
			</div>

			<ul className="flex flex-col flex-1 min-h-0 gap-y-0.5 text-xs overflow-y-auto">
				{showEllipsis && !expanded && (
					<Button
						size={'xs'}
						variant={'ghost'}
						className="flex items-center justify-center w-full"
						onClick={() => setExpanded(true)}
					>
						<Ellipsis className="size-3 text-muted-foreground" />
					</Button>
				)}
				{displayedTasks.map((task) => (
					<li
						key={task.id}
						className={cn(
							'flex gap-2 rounded px-2 py-1 items-center',
							task.state === 'completed' && 'opacity-60',
						)}
					>
						<StateIcon state={task.state} />
						<div className="flex flex-col min-w-0 gap-0.5">
							<span className="flex items-center gap-1.5">
								<span className="text-xs font-mono text-muted-foreground">
									#{task.id}
								</span>
								<span
									className={cn(
										'truncate ',
										task.state === 'completed' && 'line-through',
									)}
								>
									{task.subject}
								</span>
							</span>
							{task.blocked_by.length > 0 && (
								<span className="text-xs text-muted-foreground">
									← {t('task-panel.blockedBy')}{' '}
									{task.blocked_by.map((id) => `#${id}`).join(', ')}
								</span>
							)}
						</div>
					</li>
				))}
			</ul>
		</div>
	);
}
