import { ChevronDown } from 'lucide-react';
import { useState } from 'react';

import type { ChunkerConfig } from '@/api';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { useTranslation } from '@/i18n/useI18n';
import {
	explicitChunkerSeparators,
	formatChunkerParameterValue,
	formatChunkerSeparator,
} from '@/utils/chunker';

export function ChunkerConfigSummary({ config }: { config: ChunkerConfig }) {
	const { t } = useTranslation();
	const [expanded, setExpanded] = useState(false);
	const separators =
		config.type === 'recursive_token' &&
		Array.isArray(config.parameters.separators) &&
		config.parameters.separators.every((item) => typeof item === 'string')
			? explicitChunkerSeparators(config.parameters.separators as string[])
			: null;
	const parameters = Object.entries(config.parameters).filter(
		([key]) => !(separators && key === 'separators'),
	);

	return (
		<div className="min-w-0 space-y-2 text-sm">
			<p className="font-medium break-words">
				{t(`chunker-types.${config.type}.label`, { defaultValue: config.type })}
			</p>
			{parameters.length > 0 && (
				<dl className="flex flex-wrap gap-x-5 gap-y-1 text-xs">
					{parameters.map(([key, value]) => (
						<div key={key} className="flex min-w-0 max-w-full gap-1.5">
							<dt className="shrink-0 text-muted-foreground">
								{t(`chunker-types.${config.type}.params.${key}.label`, {
									defaultValue: key,
								})}
							</dt>
							<dd className="min-w-0 break-words tabular-nums">
								{formatChunkerParameterValue(value)}
							</dd>
						</div>
					))}
				</dl>
			)}
			{separators && (
				<Collapsible open={expanded} onOpenChange={setExpanded}>
					<div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
						<span className="text-muted-foreground">
							{t('chunker-summary.separators')}
						</span>
						<span className="tabular-nums">
							{t('chunker-summary.count', { count: separators.length })}
						</span>
						{separators.length > 0 && (
							<CollapsibleTrigger asChild>
								<Button
									type="button"
									variant="ghost"
									size="sm"
									className="h-7 gap-1 px-2 text-xs"
								>
									{t(
										expanded
											? 'chunker-summary.collapse'
											: 'chunker-summary.expand',
									)}
									<ChevronDown
										className={expanded ? 'size-3.5 rotate-180' : 'size-3.5'}
									/>
								</Button>
							</CollapsibleTrigger>
						)}
					</div>
					<CollapsibleContent>
						<p className="mt-2 text-xs text-muted-foreground">
							{t('chunker-summary.priority')}
						</p>
						<ol
							className="mt-2 flex flex-wrap gap-1.5"
							aria-label={t('chunker-summary.priority')}
						>
							{separators.map((separator, index) => (
								<li key={index} className="min-w-0 max-w-full">
									<Badge
										variant="secondary"
										className="h-auto max-w-full rounded-md py-1 font-normal whitespace-normal"
									>
										<span className="min-w-0 break-words">
											{formatChunkerSeparator(separator, t)}
										</span>
									</Badge>
								</li>
							))}
						</ol>
					</CollapsibleContent>
				</Collapsible>
			)}
			{config.type === 'recursive_token' && (
				<p className="text-xs text-muted-foreground">
					{t('chunker-summary.automaticFallback')}
				</p>
			)}
		</div>
	);
}
