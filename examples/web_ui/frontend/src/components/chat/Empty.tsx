import { MessagesSquare } from 'lucide-react';

import {
	Empty,
	EmptyDescription,
	EmptyHeader,
	EmptyMedia,
	EmptyTitle,
} from '@/components/ui/empty';
import { useTranslation } from '@/i18n/useI18n';

/**
 * Empty state component shown when there are no messages.
 * @returns An empty state element with icon and description.
 */
export function EmptyMessage() {
	const { t } = useTranslation();

	return (
		<Empty className="size-full">
			<EmptyHeader>
				<EmptyMedia variant="icon">
					<MessagesSquare />
				</EmptyMedia>
				<EmptyTitle>{t('chat.noMessages')}</EmptyTitle>
				<EmptyDescription>{t('chat.noMessagesDesc')}</EmptyDescription>
			</EmptyHeader>
		</Empty>
	);
}
