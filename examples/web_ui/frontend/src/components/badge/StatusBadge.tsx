import { CheckCircle, XCircle } from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Spinner } from '@/components/ui/spinner';
import { useTranslation } from '@/i18n/useI18n';
import { cn } from '@/lib/utils';

/**
 * Displays a status badge with icon and label based on execution status.
 * @param root0 - Component props.
 * @param root0.className - Optional CSS class.
 * @param root0.status - The execution status to display.
 * @returns A styled Badge component.
 */
export function StatusBadge({
	className,
	status,
}: {
	className?: string;
	status: 'running' | 'completed' | 'failed';
}) {
	const { t } = useTranslation();
	switch (status) {
		case 'running':
			return (
				<Badge variant="secondary" className={className}>
					<Spinner data-icon="inline-start" />
					{t('common.running')}
				</Badge>
			);
		case 'completed':
			return (
				<Badge
					className={cn(
						'bg-green-50 text-green-700 dark:bg-green-950 dark:text-green-300',
						className,
					)}
				>
					<CheckCircle className="size-5" />
					{t('common.completed')}
				</Badge>
			);
		case 'failed':
			return (
				<Badge
					className={cn(
						'bg-red-50 text-red-700 dark:bg-red-950 dark:text-red-300 border-none',
					)}
				>
					<XCircle className="size-5" />
					{t('common.failed')}
				</Badge>
			);
	}
}
