import type { TaskContext } from '@agentscope-ai/agentscope/state';

/**
 * Structural equality for TaskContext as rendered by the plan panel.
 *
 * Streaming replies re-render ChatViewport frequently. Keeping the same
 * `tasksContext` reference when task rows are unchanged prevents the
 * right-hand plan panel from flashing on every SSE flush.
 */
export function tasksContextEqual(
	a: TaskContext | null | undefined,
	b: TaskContext | null | undefined,
): boolean {
	if (a === b) return true;
	if (!a || !b) return false;
	if (a.tasks.length !== b.tasks.length) return false;
	for (let i = 0; i < a.tasks.length; i++) {
		const left = a.tasks[i];
		const right = b.tasks[i];
		if (
			left.id !== right.id ||
			left.state !== right.state ||
			left.subject !== right.subject
		) {
			return false;
		}
		const lb = left.blocked_by;
		const rb = right.blocked_by;
		if (lb.length !== rb.length) return false;
		for (let j = 0; j < lb.length; j++) {
			if (lb[j] !== rb[j]) return false;
		}
	}
	return true;
}
