import type { ContentBlock } from '@agentscope-ai/agentscope/message';
import { ChevronDown, ChevronUp, FileText } from 'lucide-react';
import { useMemo, useState } from 'react';

import { extractArtifacts, type FileArtifact } from './artifacts';
import { Button } from '@/components/ui/button';
import { useTranslation } from '@/i18n/useI18n';

export function ArtifactList({
	content,
	onOpen,
}: {
	content: ContentBlock[];
	onOpen: (artifact: FileArtifact) => void;
}) {
	const { t } = useTranslation();
	const artifacts = useMemo(() => extractArtifacts(content), [content]);
	const [expanded, setExpanded] = useState(false);
	if (!artifacts.length) return null;
	return (
		<section className="my-3 w-full min-w-0" aria-label={t('artifacts.title')}>
			<div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
				{artifacts.map((artifact, index) => (
					<button
						key={artifact.path}
						type="button"
						onClick={(event) => {
							event.currentTarget.focus();
							onOpen(artifact);
						}}
						title={artifact.path}
						className={`flex min-w-0 items-center gap-3 rounded-xl border bg-background p-3 text-left transition-colors hover:bg-muted/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
							!expanded && index >= 4
								? 'hidden'
								: !expanded && index >= 2
									? 'hidden sm:flex'
									: ''
						}`}
					>
						<FileText className="size-5 shrink-0 text-muted-foreground" />
						<span className="min-w-0 flex-1">
							<span className="block truncate text-sm font-medium">
								{artifact.name}
							</span>
							<span className="block truncate text-xs text-muted-foreground">
								{artifact.path}
							</span>
						</span>
						{artifact.change && (
							<span className="shrink-0 text-xs text-muted-foreground">
								{t(`artifacts.${artifact.change}`)}
							</span>
						)}
					</button>
				))}
			</div>
			{artifacts.length > 2 && (
				<Button
					type="button"
					variant="ghost"
					size="sm"
					className={`mt-1 text-muted-foreground ${artifacts.length <= 4 ? 'sm:hidden' : ''}`}
					aria-expanded={expanded}
					onClick={() => setExpanded((value) => !value)}
				>
					{expanded ? <ChevronUp /> : <ChevronDown />}
					{t(expanded ? 'artifacts.collapse' : 'artifacts.expand')}
				</Button>
			)}
		</section>
	);
}
