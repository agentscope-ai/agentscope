import type { Task, TaskContext } from '@agentscope-ai/agentscope/state';
import { CheckCircle2, Circle, Loader2 } from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { useTranslation } from '@/i18n/useI18n';
import { cn } from '@/lib/utils';

interface TaskPanelProps {
	/**
	 * The task context to render. Pass ``null`` when no data is
	 * available yet (renders nothing).
	 */
	tasksContext: TaskContext | null;
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
			return <CheckCircle2 className="size-4 text-green-500 shrink-0" />;
		case 'in_progress':
			return <Loader2 className="size-4 animate-spin text-blue-500 shrink-0" />;
		default:
			return <Circle className="size-4 text-muted-foreground shrink-0" />;
	}
}

/**
 * Compact, read-only panel listing the agent's current tasks with
 * their status and dependency information.
 *
 * Each row shows ``#id  [icon]  subject  [← blocked by #x, #y]``.
 * The panel header displays a progress summary like ``Tasks (3/5)``.
 *
 * @param tasksContext - The full ``TaskContext`` from ``AgentState``.
 *   ``null`` hides the panel entirely.
 * @returns A collapsible panel element, or ``null`` when there are no
 *   tasks.
 */
export function TaskPanel({ tasksContext }: TaskPanelProps) {
	const { t } = useTranslation();

	if (!tasksContext || tasksContext.tasks.length === 0) {
		return null;
	}

	const { tasks } = tasksContext;
	const completed = tasks.filter((t) => t.state === 'completed').length;

	return (
		<div className="flex flex-col gap-1 rounded-md border bg-background p-3 text-sm">
			<header className="flex items-center justify-between px-1 pb-1">
				<span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
					{t('task-panel.heading')}
				</span>
				<Badge variant="secondary" className="text-xs tabular-nums">
					{completed}/{tasks.length}
				</Badge>
			</header>
			<ul className="flex flex-col gap-0.5">
				{tasks.map((task) => (
					<li
						key={task.id}
						className={cn(
							'flex items-start gap-2 rounded px-2 py-1',
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
										'truncate',
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
