import { ChevronDown, PlusCircle } from 'lucide-react';

import type { EmbeddingModelCard, KbEmbeddingProvider } from '@/api';
import { Button } from '@/components/ui/button';
import {
	DropdownMenu,
	DropdownMenuContent,
	DropdownMenuGroup,
	DropdownMenuItem,
	DropdownMenuLabel,
	DropdownMenuSeparator,
	DropdownMenuSub,
	DropdownMenuSubContent,
	DropdownMenuSubTrigger,
	DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { useTranslation } from '@/i18n/useI18n.ts';

interface SelectedEmbedding {
	type: string;
	credentialId: string;
	model: string;
	card: EmbeddingModelCard;
}

interface Props {
	value?: { type: string; credential_id: string; model: string } | null;
	providers: KbEmbeddingProvider[];
	loading?: boolean;
	onChange?: (selected: SelectedEmbedding) => void;
	onAddCredential?: () => void;
	/** Override the trigger label shown when no model is selected. */
	placeholder?: string;
}

/**
 * Embedding model picker. Consumes a pre-grouped, server-filtered
 * list of providers (one entry per credential) and emits the chosen
 * model's full :class:`EmbeddingModelCard` so the caller can derive
 * the dimension separately.
 */
export function EmbeddingSelect({
	value,
	providers,
	loading,
	onChange,
	onAddCredential,
	placeholder,
}: Props) {
	const { t } = useTranslation();

	// Group providers by credential type for the existing two-level UI
	// (provider type → credential → models).
	const groups: Record<string, KbEmbeddingProvider[]> = {};
	for (const provider of providers) {
		const type = (provider.credential.data.type as string) ?? 'unknown';
		if (!groups[type]) groups[type] = [];
		groups[type].push(provider);
	}
	const hasOptions = Object.keys(groups).length > 0;

	const handleSelect = (type: string, credentialId: string, card: EmbeddingModelCard) => {
		onChange?.({ type, credentialId, model: card.name, card });
	};

	const displayLabel = value?.model
		? value.model
		: loading
			? t('embedding-select.loading')
			: (placeholder ?? t('embedding-select.placeholder'));

	return (
		<DropdownMenu>
			<DropdownMenuTrigger asChild>
				<Button variant="outline" size="sm" className="justify-between gap-1">
					<span className="truncate">{displayLabel}</span>
					<ChevronDown className="size-3.5 opacity-50" />
				</Button>
			</DropdownMenuTrigger>
			<DropdownMenuContent align="start" className="min-w-48 max-h-72 overflow-y-auto">
				{!loading && !hasOptions ? (
					<div className="px-2 py-3 text-center text-sm text-muted-foreground">
						<p className="font-medium">{t('embedding-select.empty.title')}</p>
						<p className="text-xs mt-1">{t('embedding-select.empty.description')}</p>
					</div>
				) : (
					Object.entries(groups).map(([type, items], idx) => {
						const isSingle = items.length === 1;
						return (
							<DropdownMenuGroup key={type}>
								{idx > 0 && <DropdownMenuSeparator />}
								<DropdownMenuLabel>
									{type.replace(/_credential$/, '')}
								</DropdownMenuLabel>
								{isSingle
									? items[0].models.map((m) => (
											<DropdownMenuItem
												key={m.name}
												onSelect={() =>
													handleSelect(type, items[0].credential.id, m)
												}
											>
												{m.label}
											</DropdownMenuItem>
										))
									: items.map(({ credential, models }) => {
											const credName =
												(credential.data.name as string) ||
												credential.id.slice(0, 8);
											return (
												<DropdownMenuSub key={credential.id}>
													<DropdownMenuSubTrigger>
														{credName}
													</DropdownMenuSubTrigger>
													<DropdownMenuSubContent className="max-h-60 overflow-y-auto">
														{models.map((m) => (
															<DropdownMenuItem
																key={m.name}
																onSelect={() =>
																	handleSelect(
																		type,
																		credential.id,
																		m,
																	)
																}
															>
																{m.label}
															</DropdownMenuItem>
														))}
													</DropdownMenuSubContent>
												</DropdownMenuSub>
											);
										})}
							</DropdownMenuGroup>
						);
					})
				)}
				<DropdownMenuSeparator />
				<DropdownMenuItem onSelect={onAddCredential}>
					<PlusCircle className="size-4" />
					<span>{t('embedding-select.addCredential')}</span>
				</DropdownMenuItem>
			</DropdownMenuContent>
		</DropdownMenu>
	);
}
