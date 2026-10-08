import { AudioLines, Ban, ChevronDown, PlusCircle } from 'lucide-react';

import type { RealtimeModelCard, RealtimeModelConfig } from '@/api';
import { Button } from '@/components/ui/button';
import {
	DropdownMenu,
	DropdownMenuContent,
	DropdownMenuGroup,
	DropdownMenuItem,
	DropdownMenuLabel,
	DropdownMenuPortal,
	DropdownMenuSeparator,
	DropdownMenuSub,
	DropdownMenuSubContent,
	DropdownMenuSubTrigger,
	DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { useAvailableRealtimeModels } from '@/hooks/useAvailableRealtimeModels';
import { useTranslation } from '@/i18n/useI18n';
import { cn } from '@/lib/utils';
import { credentialLabel } from '@/utils/common';

interface Props extends Omit<React.ComponentPropsWithoutRef<typeof Button>, 'onChange' | 'value'> {
	value?: RealtimeModelConfig | null;
	onChange?: (value: RealtimeModelConfig | null) => void;
	onAddCredential?: () => void;
}

function parameterDefaults(model: RealtimeModelCard): Record<string, unknown> {
	const schema = model.parameter_schema as {
		properties?: Record<string, { default?: unknown }>;
	};
	return Object.fromEntries(
		Object.entries(schema.properties ?? {})
			.filter(([, property]) => property.default !== undefined)
			.map(([name, property]) => [name, property.default]),
	);
}

export function RealtimeModelSelect({
	value,
	onChange,
	onAddCredential,
	className,
	...props
}: Props) {
	const { groups, loading } = useAvailableRealtimeModels();
	const { t } = useTranslation();
	const entries = Object.entries(groups).sort(([left], [right]) => left.localeCompare(right));

	const handleSelect = (credentialId: string, model: RealtimeModelCard) => {
		onChange?.({
			type: model.model_type,
			credential_id: credentialId,
			model: model.name,
			parameters: parameterDefaults(model),
		});
	};

	return (
		<DropdownMenu>
			<DropdownMenuTrigger asChild>
				<Button
					variant="ghost"
					size="sm"
					className={cn('max-w-52 justify-between gap-1 font-mono', className)}
					{...props}
				>
					<AudioLines className="size-4 shrink-0" />
					<span className="hidden max-w-32 truncate lg:inline">
						{value?.model ?? t('realtime.selectModel')}
					</span>
					<ChevronDown className="size-3.5 shrink-0 text-muted-foreground" />
				</Button>
			</DropdownMenuTrigger>
			<DropdownMenuContent align="end" className="min-w-56 max-h-72 overflow-y-auto">
				{!loading && entries.length === 0 ? (
					<div className="px-3 py-4 text-center text-sm text-muted-foreground">
						{t('realtime.noModels')}
					</div>
				) : (
					entries.map(([provider, items], index) => (
						<DropdownMenuGroup key={provider}>
							{index > 0 && <DropdownMenuSeparator />}
							<DropdownMenuLabel>
								{provider.replace(/_credential$/, '')}
							</DropdownMenuLabel>
							{items.length === 1
								? items[0].models.map((model) => (
										<DropdownMenuItem
											key={`${model.model_type}:${model.name}`}
											onSelect={() =>
												handleSelect(items[0].credential.id, model)
											}
										>
											{model.name}
										</DropdownMenuItem>
									))
								: items.map(({ credential, models }) => (
										<DropdownMenuSub key={credential.id}>
											<DropdownMenuSubTrigger>
												{credentialLabel(credential)}
											</DropdownMenuSubTrigger>
											<DropdownMenuPortal>
												<DropdownMenuSubContent>
													{models.map((model) => (
														<DropdownMenuItem
															key={`${model.model_type}:${model.name}`}
															onSelect={() =>
																handleSelect(credential.id, model)
															}
														>
															{model.label}
														</DropdownMenuItem>
													))}
												</DropdownMenuSubContent>
											</DropdownMenuPortal>
										</DropdownMenuSub>
									))}
						</DropdownMenuGroup>
					))
				)}
				<DropdownMenuSeparator />
				<DropdownMenuItem onSelect={() => onChange?.(null)} disabled={!value}>
					<Ban className="size-4" />
					{t('realtime.disable')}
				</DropdownMenuItem>
				<DropdownMenuItem onSelect={onAddCredential}>
					<PlusCircle className="size-4" />
					{t('llm-select.addCredential')}
				</DropdownMenuItem>
			</DropdownMenuContent>
		</DropdownMenu>
	);
}
