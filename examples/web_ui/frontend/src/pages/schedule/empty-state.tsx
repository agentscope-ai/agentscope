import { Calendar } from 'lucide-react';
import type { ReactNode } from 'react';

import {
	Empty,
	EmptyContent,
	EmptyDescription,
	EmptyHeader,
	EmptyMedia,
	EmptyTitle,
} from '@/components/ui/empty';
import { useTranslation } from '@/i18n/useI18n';

interface EmptyStateProps {
	title?: string;
	description?: string;
	action?: ReactNode;
}

/**
 * Empty state component for schedule page
 *
 * @param root0 - Component props
 * @param root0.title - Title text
 * @param root0.description - Description text
 * @param root0.action - Action element (e.g., button)
 * @returns EmptyState component
 */
export function EmptyState({ title, description, action }: EmptyStateProps) {
	const { t } = useTranslation();

	return (
		<Empty className="size-full">
			<EmptyHeader>
				<EmptyMedia variant="icon">
					<Calendar />
				</EmptyMedia>
				<EmptyTitle>{title || t('schedule.noSchedules')}</EmptyTitle>
				<EmptyDescription>
					{description || t('schedule.noSchedulesDescription')}
				</EmptyDescription>
			</EmptyHeader>
			<EmptyContent>{action}</EmptyContent>
		</Empty>
	);
}
