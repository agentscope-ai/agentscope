import { PlugZap } from 'lucide-react';

import { ManualClientToolCard } from './ManualClientToolCard';
import { useTranslation } from '@/i18n/useI18n';
import {
	getClientExternalToolRegistration,
	type PendingClientExternalToolProps,
} from '@/lib/client-external-tools';

export function ExternalExecutionCard(props: PendingClientExternalToolProps) {
	const { toolCall } = props;
	const { t } = useTranslation();
	const Component = getClientExternalToolRegistration(toolCall.name)?.PendingComponent;
	if (Component) return <Component {...props} />;
	if (toolCall.name.startsWith('client__')) return <ManualClientToolCard {...props} />;
	return (
		<div className="rounded-[28px] bg-muted px-6 py-5 text-sm ring ring-border">
			<div className="flex items-center gap-2 font-medium text-secondary-foreground">
				<PlugZap className="size-4 shrink-0" />
				{t('chat.externalExecution.title', { name: toolCall.name })}
			</div>
			<p className="mt-2 text-muted-foreground">{t('chat.externalExecution.description')}</p>
		</div>
	);
}
