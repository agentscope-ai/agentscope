import type { LucideIcon } from 'lucide-react';

import {
	Empty,
	EmptyDescription,
	EmptyHeader,
	EmptyMedia,
	EmptyTitle,
} from '@/components/ui/empty';
import { cn } from '@/lib/utils';

interface PanelEmptyProps {
	/** Icon communicating the kind of emptiness (no data vs no results). */
	icon: LucideIcon;
	/** Short heading, e.g. "No MCP servers" or "No results". */
	title: string;
	/** Optional secondary explanation line. */
	description?: string;
	className?: string;
}

/**
 * A reusable empty-state for dock panels. Fills the remaining vertical
 * space and centers an icon + title + optional description. Callers
 * distinguish a "no data yet" state from a "search found nothing"
 * state via the {@link icon} and {@link title} they pass.
 *
 * @param icon - The lucide icon to show.
 * @param title - The empty-state heading.
 * @param description - Optional explanatory text.
 * @param className - Extra classes for the wrapper.
 * @returns The centered empty-state element.
 */
export function PanelEmpty({ icon: Icon, title, description, className }: PanelEmptyProps) {
	return (
		<Empty className={cn('flex-1 border-0 p-4', className)}>
			<EmptyHeader>
				<EmptyMedia variant="icon">
					<Icon />
				</EmptyMedia>
				<EmptyTitle>{title}</EmptyTitle>
				{description ? <EmptyDescription>{description}</EmptyDescription> : null}
			</EmptyHeader>
		</Empty>
	);
}
