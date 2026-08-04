import { CircleAlert } from 'lucide-react';

import type { SeedErrors as SeedErrorMap } from '@/api';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert.tsx';
import { useTranslation } from '@/i18n/useI18n.ts';

interface SeedErrorsProps {
	/** Names the agent came with that are not here, mapped to why. */
	errors: SeedErrorMap;
}

/**
 * Why something the agent comes with is missing from this workspace.
 *
 * The workspace is seeded once, when it is first created, so a failure
 * there leaves no trace in the list itself — a stateful MCP that would
 * not connect is simply absent, not red. This is the only place that
 * reason surfaces.
 */
export function SeedErrors({ errors }: SeedErrorsProps) {
	const { t } = useTranslation();
	const entries = Object.entries(errors);
	if (entries.length === 0) return null;

	return (
		<Alert variant="destructive">
			<CircleAlert />
			<AlertTitle>{t('panel.seedErrors.title')}</AlertTitle>
			<AlertDescription>
				<ul className="list-disc space-y-1 pl-4">
					{entries.map(([name, why]) => (
						<li key={name} className="break-words">
							<span className="font-medium">{name}</span>: {why}
						</li>
					))}
				</ul>
			</AlertDescription>
		</Alert>
	);
}
