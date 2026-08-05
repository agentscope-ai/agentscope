import { ChevronDown, Ban } from 'lucide-react';
import React from 'react';

import type { ChatModelConfig, RealtimeModelCard } from '@/api';
import { Button } from '@/components/ui/button';
import {
	DropdownMenu,
	DropdownMenuCheckboxItem,
	DropdownMenuContent,
	DropdownMenuGroup,
	DropdownMenuItem,
	DropdownMenuLabel,
	DropdownMenuRadioGroup,
	DropdownMenuRadioItem,
	DropdownMenuSeparator,
	DropdownMenuSub,
	DropdownMenuSubContent,
	DropdownMenuSubTrigger,
	DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { useAvailableRealtimeModels } from '@/hooks/useAvailableRealtimeModels';
import { useTranslation } from '@/i18n/useI18n';

interface ParameterProperty {
	type?: string;
	title?: string;
	description?: string;
	default?: unknown;
	minimum?: number;
	maximum?: number;
	enum?: unknown[];
	anyOf?: ParameterProperty[];
}

interface ParameterSchema {
	type?: string;
	properties?: Record<string, ParameterProperty>;
	required?: string[];
}

function resolveType(prop: ParameterProperty): { type: string; enumValues: unknown[] | null } {
	if (prop.type) return { type: prop.type, enumValues: prop.enum ?? null };
	for (const v of prop.anyOf ?? []) {
		if (v.type && v.type !== 'null')
			return { type: v.type, enumValues: v.enum ?? prop.enum ?? null };
	}
	return { type: 'string', enumValues: null };
}

interface Props {
	value: ChatModelConfig | null;
	onChange: (value: ChatModelConfig | null) => void;
}

export function RealtimeModelSelect({ value, onChange }: Props) {
	const { groups, loading } = useAvailableRealtimeModels();
	const { t } = useTranslation();
	const hasOptions = Object.keys(groups).length > 0;

	const handleSelect = (type: string, credentialId: string, model: RealtimeModelCard) => {
		const schema = model.parameter_schema as ParameterSchema | undefined;
		const defaults: Record<string, unknown> = {};
		if (schema?.properties) {
			for (const [k, p] of Object.entries(schema.properties)) {
				if (p.default !== undefined) defaults[k] = p.default;
			}
		}
		onChange({ type, credential_id: credentialId, model: model.name, parameters: defaults });
	};

	const selectedCard = (() => {
		if (!value) return null;
		const items = groups[value.type];
		if (!items) return null;
		for (const { credential, models } of items) {
			if (credential.id !== value.credential_id) continue;
			const found = models.find((m) => m.name === value.model);
			if (found) return found;
		}
		return null;
	})();

	const displayLabel = value?.model
		? value.model
		: loading
			? t('llm-select.loading')
			: t('model-parameters.realtimeLabel');

	return (
		<DropdownMenu>
			<DropdownMenuTrigger asChild>
				<Button variant="outline" size="sm" className="w-48 justify-between gap-1">
					<span className="truncate">{displayLabel}</span>
					<ChevronDown className="size-3.5 opacity-50 shrink-0" />
				</Button>
			</DropdownMenuTrigger>
			<DropdownMenuContent align="start" className="min-w-48 max-h-96 overflow-y-auto">
				{!loading && !hasOptions ? (
					<div className="px-2 py-3 text-center text-sm text-muted-foreground">
						<p>{t('model-parameters.realtimeEmpty')}</p>
					</div>
				) : (
					Object.entries(groups).map(([type, items], idx) => (
						<DropdownMenuGroup key={type}>
							{idx > 0 && <DropdownMenuSeparator />}
							<DropdownMenuLabel>
								{type.replace(/_credential$/, '')}
							</DropdownMenuLabel>
							{items.flatMap(({ credential, models }) =>
								models.map((m) => {
									const isSelected =
										value?.credential_id === credential.id &&
										value?.model === m.name;
									return (
										<DropdownMenuCheckboxItem
											key={`${credential.id}-${m.name}`}
											checked={isSelected}
											onSelect={(e) => e.preventDefault()}
											onCheckedChange={(checked) => {
												if (checked) handleSelect(type, credential.id, m);
											}}
										>
											{m.label}
										</DropdownMenuCheckboxItem>
									);
								}),
							)}
						</DropdownMenuGroup>
					))
				)}

				{/* Parameter editing sub-menu — always rendered to avoid layout shift */}
				<DropdownMenuSeparator />
				<DropdownMenuSub>
					<DropdownMenuSubTrigger disabled={!value || !selectedCard}>
						{t('model-parameters.parametersLabel')}
					</DropdownMenuSubTrigger>
					{value &&
						selectedCard &&
						(() => {
							const mSchema = selectedCard.parameter_schema as
								| ParameterSchema
								| undefined;
							const mProps = mSchema?.properties ?? {};
							const mRequired = mSchema?.required ?? [];
							const mEntries = Object.entries(mProps);
							if (mEntries.length === 0) return null;
							const curParams = value.parameters ?? {};

							return (
								<DropdownMenuSubContent
									className="w-80 max-h-96 overflow-y-auto p-3"
									sideOffset={2}
									alignOffset={-4}
								>
									<div className="mb-3">
										<p className="text-sm font-medium">
											{t('model-parameters.title')}
										</p>
										<p className="text-muted-foreground text-xs">
											{t('model-parameters.realtimeParametersDescription')}
										</p>
									</div>
									<div
										className="grid grid-cols-[auto_1fr] items-center gap-x-3 gap-y-3"
										onPointerDown={(e) => e.stopPropagation()}
										onKeyDown={(e) => e.stopPropagation()}
									>
										{mEntries.map(([key, prop]) => {
											const { type: effectiveType, enumValues } =
												resolveType(prop);
											const label = prop.title ?? key;
											const isReq = mRequired.includes(key);

											const handleParamChange = (v: unknown) => {
												const next = { ...curParams, [key]: v };
												if (v === '' || v === undefined) delete next[key];
												onChange({ ...value, parameters: next });
											};

											let field: React.ReactNode;
											const fieldId = `rt-${selectedCard.name}-${key}`;

											if (effectiveType === 'boolean') {
												field = (
													<>
														<Label
															htmlFor={fieldId}
															className="whitespace-nowrap"
														>
															{label}
														</Label>
														<Switch
															id={fieldId}
															checked={
																curParams[key] !== undefined
																	? !!curParams[key]
																	: !!prop.default
															}
															onCheckedChange={(checked) =>
																handleParamChange(!!checked)
															}
														/>
													</>
												);
											} else if (enumValues) {
												const displayValue =
													curParams[key] !== undefined &&
													curParams[key] !== null
														? String(curParams[key])
														: '';
												field = (
													<>
														<Label
															htmlFor={fieldId}
															className="whitespace-nowrap"
														>
															{label}
															{isReq && (
																<span className="text-destructive ml-0.5">
																	*
																</span>
															)}
														</Label>
														<DropdownMenu>
															<DropdownMenuTrigger asChild>
																<Button
																	id={fieldId}
																	variant="outline"
																	className="w-full justify-between gap-1"
																>
																	<span className="truncate">
																		{displayValue}
																	</span>
																	<ChevronDown className="size-3.5 opacity-50 shrink-0" />
																</Button>
															</DropdownMenuTrigger>
															<DropdownMenuContent
																align="start"
																className="max-h-60 overflow-y-auto"
																onPointerDown={(e) =>
																	e.stopPropagation()
																}
															>
																<DropdownMenuRadioGroup
																	value={displayValue}
																	onValueChange={(v) =>
																		handleParamChange(v)
																	}
																>
																	{enumValues.map((opt) => (
																		<DropdownMenuRadioItem
																			key={String(opt)}
																			value={String(opt)}
																		>
																			{String(opt)}
																		</DropdownMenuRadioItem>
																	))}
																</DropdownMenuRadioGroup>
															</DropdownMenuContent>
														</DropdownMenu>
													</>
												);
											} else if (
												effectiveType === 'number' ||
												effectiveType === 'integer'
											) {
												field = (
													<>
														<Label
															htmlFor={fieldId}
															className="whitespace-nowrap"
														>
															{label}
															{isReq && (
																<span className="text-destructive ml-0.5">
																	*
																</span>
															)}
														</Label>
														<Input
															id={fieldId}
															type="number"
															value={
																curParams[key] !== undefined
																	? String(curParams[key])
																	: ''
															}
															placeholder={
																prop.default != null
																	? String(prop.default)
																	: undefined
															}
															min={prop.minimum}
															max={prop.maximum}
															step={
																effectiveType === 'number'
																	? 'any'
																	: undefined
															}
															onChange={(e) => {
																const raw = e.target.value;
																handleParamChange(
																	raw === ''
																		? undefined
																		: Number(raw),
																);
															}}
														/>
													</>
												);
											} else {
												field = (
													<>
														<Label
															htmlFor={fieldId}
															className="whitespace-nowrap"
														>
															{label}
															{isReq && (
																<span className="text-destructive ml-0.5">
																	*
																</span>
															)}
														</Label>
														<Input
															id={fieldId}
															type="text"
															value={
																curParams[key] !== undefined
																	? String(curParams[key])
																	: ''
															}
															placeholder={
																prop.default != null
																	? String(prop.default)
																	: undefined
															}
															onChange={(e) =>
																handleParamChange(e.target.value)
															}
														/>
													</>
												);
											}

											return (
												<Tooltip key={key}>
													<TooltipTrigger asChild>
														<div className="col-span-2 grid grid-cols-subgrid items-center">
															{field}
														</div>
													</TooltipTrigger>
													{prop.description && (
														<TooltipContent
															side="left"
															className="max-w-64"
														>
															{prop.description}
														</TooltipContent>
													)}
												</Tooltip>
											);
										})}
									</div>
								</DropdownMenuSubContent>
							);
						})()}
				</DropdownMenuSub>

				<DropdownMenuSeparator />
				<DropdownMenuItem
					disabled={!value}
					onSelect={(e) => {
						e.preventDefault();
						onChange(null);
					}}
				>
					<Ban className="size-4 mr-2 opacity-60" />
					{t('model-parameters.noRealtime')}
				</DropdownMenuItem>
			</DropdownMenuContent>
		</DropdownMenu>
	);
}
