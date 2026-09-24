import { FileX, Search, SearchX } from 'lucide-react';
import { useMemo, useState } from 'react';

import type { KnowledgeBaseView, SessionKnowledgeConfig } from '@/api';
import { PanelEmpty } from '@/components/panel/PanelEmpty';
import { Badge } from '@/components/ui/badge';
import { Checkbox } from '@/components/ui/checkbox';
import { InputGroup, InputGroupAddon, InputGroupInput } from '@/components/ui/input-group';
import { Item, ItemContent, ItemDescription, ItemTitle } from '@/components/ui/item';
import { useTranslation } from '@/i18n/useI18n';

interface KnowledgeBasePanelProps {
	/** The user's knowledge bases. */
	knowledgeBases: KnowledgeBaseView[];
	/** Whether the KB list is still loading. */
	loading?: boolean;
	/**
	 * Current attachment for this session. `null` means no KBs attached
	 * and the panel renders with an empty selection.
	 */
	value: SessionKnowledgeConfig | null;
	/**
	 * Persist a new attachment to the session. The owner is responsible
	 * for awaiting the backend round-trip and refreshing session state.
	 * Pass `null` to detach every KB.
	 */
	onChange: (next: SessionKnowledgeConfig | null) => void;
	/** Disable the entire panel — e.g. when no session is selected. */
	disabled?: boolean;
}

/**
 * Pure content body for the Knowledge Base dock panel: a search box and
 * a checkbox list of the user's KBs.
 *
 * Middleware parameter editing lives in
 * {@link KnowledgeBaseParametersPopover}, which the owner mounts in the
 * panel header's `actions` slot — that keeps this body focused on
 * picking KBs rather than mixing two unrelated forms in the same scroll
 * area.
 */
export function KnowledgeBasePanel({
	knowledgeBases,
	loading = false,
	value,
	onChange,
	disabled = false,
}: KnowledgeBasePanelProps) {
	const { t } = useTranslation();
	const [search, setSearch] = useState('');

	const selectedIds = useMemo(() => new Set(value?.knowledge_base_ids ?? []), [value]);

	const filtered = search
		? knowledgeBases.filter((kb) => kb.name.toLowerCase().includes(search.toLowerCase()))
		: knowledgeBases;

	const toggleKb = (kbId: string, checked: boolean) => {
		const next = new Set(selectedIds);
		if (checked) next.add(kbId);
		else next.delete(kbId);
		const ids = Array.from(next);
		if (ids.length === 0) {
			onChange(null);
			return;
		}
		onChange({
			knowledge_base_ids: ids,
			parameters: value?.parameters ?? {},
		});
	};

	return (
		<div className="flex flex-col flex-1 min-h-0 gap-y-2">
			<span className="text-muted-foreground text-sm">
				{t('panel.knowledge.description')}
			</span>
			<InputGroup>
				<InputGroupInput
					placeholder={t('panel.knowledge.searchPlaceholder')}
					value={search}
					onChange={(e) => setSearch(e.target.value)}
					disabled={disabled}
				/>
				<InputGroupAddon align="inline-end">
					<Search />
				</InputGroupAddon>
			</InputGroup>

			<div className="flex flex-col flex-1 min-h-0 gap-y-3 overflow-y-auto">
				{loading ? (
					<div className="flex flex-1 items-center justify-center">
						<p className="text-muted-foreground text-sm">{t('panel.loading')}</p>
					</div>
				) : filtered.length === 0 ? (
					<PanelEmpty
						icon={search ? SearchX : FileX}
						title={
							search ? t('panel.search.emptyTitle') : t('panel.knowledge.emptyTitle')
						}
						description={
							search
								? t('panel.search.emptyDescription', { query: search })
								: t('panel.knowledge.emptyDescription')
						}
					/>
				) : (
					<div className="flex flex-col gap-y-2">
						{filtered.map((kb) => {
							const isSelected = selectedIds.has(kb.id);
							const inputId = `kb-${kb.id}`;
							return (
								<Item
									key={kb.id}
									variant="outline"
									data-selected={isSelected || undefined}
								>
									<Checkbox
										id={inputId}
										checked={isSelected}
										disabled={disabled}
										onCheckedChange={(checked) => toggleKb(kb.id, !!checked)}
									/>
									<ItemContent>
										<ItemTitle>
											<label htmlFor={inputId} className="cursor-pointer">
												{kb.name}
											</label>
										</ItemTitle>
										{kb.description ? (
											<ItemDescription>{kb.description}</ItemDescription>
										) : null}
										<div className="flex flex-wrap gap-1 mt-1">
											<Badge
												variant="outline"
												className="text-[10px] px-1 py-0"
											>
												{kb.embedding_model_config.model}
											</Badge>
											<Badge
												variant="outline"
												className="text-[10px] px-1 py-0"
											>
												{kb.embedding_model_config.dimensions}d
											</Badge>
										</div>
									</ItemContent>
								</Item>
							);
						})}
					</div>
				)}
			</div>
		</div>
	);
}
