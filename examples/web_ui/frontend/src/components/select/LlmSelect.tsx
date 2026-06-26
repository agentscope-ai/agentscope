import { ChevronDown, PlusCircle, Ban } from 'lucide-react';
import { useEffect } from 'react';

import type { ChatModelConfig } from '@/api';
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
import { useAvailableModels } from '@/hooks/useAvailableModels';
import { useTranslation } from '@/i18n/useI18n.ts';

interface Props {
	value?: ChatModelConfig | null;
	/**
	 * Called when the user selects a model, or — when `allowClear` is true —
	 * clears the selection (in which case `null` is emitted).
	 */
	onChange?: (value: ChatModelConfig | null) => void;
	onAddCredential?: () => void;
	refetchTrigger?: number;
	/** Override the trigger label shown when no model is selected. */
	placeholder?: string;
	/**
	 * When true, append a "clear selection" item to the dropdown that emits
	 * `null` via `onChange`. Used by the fallback selector.
	 */
	allowClear?: boolean;
	/** Override the label of the "clear selection" item. */
	clearLabel?: string;
}

export function LlmSelect({
	value,
	onChange,
	onAddCredential,
	refetchTrigger,
	placeholder,
	allowClear = false,
	clearLabel,
}: Props) {
	const { groups, loading, refetch } = useAvailableModels();
	const { t } = useTranslation();
	const hasOptions = Object.keys(groups).length > 0;

	useEffect(() => {
		if (refetchTrigger !== undefined && refetchTrigger > 0) refetch();
	}, [refetchTrigger, refetch]);

	const handleSelect = (
		type: string,
		credentialId: string,
		model: string,
		modelClass: string,
	) => {
		onChange?.({
			type,
			credential_id: credentialId,
			model,
			model_class: modelClass,
			parameters: {},
		});
	};

	const displayLabel = (() => {
		if (!value?.model) {
			return loading ? t('llm-select.loading') : (placeholder ?? t('llm-select.placeholder'));
		}
		const items = groups[value.type];
		if (items) {
			for (const { models } of items) {
				const card = models.find(
					(m) =>
						m.name === value.model &&
						(!value.model_class || m.model_class === value.model_class),
				);
				if (card) return card.label;
			}
		}
		return value.model;
	})();

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
						<p className="font-medium">{t('llm-select.empty.title')}</p>
						<p className="text-xs mt-1">{t('llm-select.empty.description')}</p>
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
												key={`${m.model_class}:${m.name}`}
												onSelect={() =>
													handleSelect(
														type,
														items[0].credential.id,
														m.name,
														m.model_class,
													)
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
																key={`${m.model_class}:${m.name}`}
																onSelect={() =>
																	handleSelect(
																		type,
																		credential.id,
																		m.name,
																		m.model_class,
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
				{allowClear && (
					<DropdownMenuItem onSelect={() => onChange?.(null)} disabled={!value}>
						<Ban className="size-4" />
						<span>{clearLabel ?? t('llm-select.clear')}</span>
					</DropdownMenuItem>
				)}
				<DropdownMenuItem onSelect={onAddCredential}>
					<PlusCircle className="size-4" />
					<span>{t('llm-select.addCredential')}</span>
				</DropdownMenuItem>
			</DropdownMenuContent>
		</DropdownMenu>
	);
}
