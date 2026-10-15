import { Check, ChevronDown, SlidersHorizontal } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';

import type {
	ASRModelCard,
	ASRModelConfig,
	ChatModelConfig,
	CredentialView,
	ModelCard,
	RealtimeModelCard,
	RealtimeModelConfig,
	TTSModelCard,
	TTSModelConfig,
} from '@/api';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
	DropdownMenu,
	DropdownMenuCheckboxItem,
	DropdownMenuContent,
	DropdownMenuGroup,
	DropdownMenuLabel,
	DropdownMenuPortal,
	DropdownMenuRadioGroup,
	DropdownMenuRadioItem,
	DropdownMenuSeparator,
	DropdownMenuSub,
	DropdownMenuSubContent,
	DropdownMenuSubTrigger,
	DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu.tsx';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { type CredentialWithASRModels, useAvailableASRModels } from '@/hooks/useAvailableASRModels';
import { useAvailableModels } from '@/hooks/useAvailableModels';
import { useAvailableTTSModels } from '@/hooks/useAvailableTTSModels';
import { useTranslation } from '@/i18n/useI18n';
import { cn } from '@/lib/utils';
import { credentialLabel } from '@/utils/common';

interface ParameterProperty {
	type?: string;
	title?: string;
	description?: string;
	default?: unknown;
	minimum?: number;
	maximum?: number;
	exclusiveMinimum?: number;
	exclusiveMaximum?: number;
	enum?: unknown[];
	anyOf?: ParameterProperty[];
}

interface ParameterSchema {
	title?: string;
	description?: string;
	type?: string;
	properties?: Record<string, ParameterProperty>;
	required?: string[];
}

interface ResolvedType {
	type: string;
	enumValues: unknown[] | null;
}

/** Resolve a property's effective scalar type and enum values, looking
 *  through ``anyOf`` and ignoring ``null`` variants. */
function resolveType(prop: ParameterProperty): ResolvedType {
	if (prop.type) {
		return { type: prop.type, enumValues: prop.enum ?? null };
	}
	for (const variant of prop.anyOf ?? []) {
		if (variant.type && variant.type !== 'null') {
			return {
				type: variant.type,
				enumValues: variant.enum ?? prop.enum ?? null,
			};
		}
	}
	return { type: 'string', enumValues: null };
}

/** Extract default values from a model card's parameter schema. */
function extractDefaults(schema: ParameterSchema | undefined): Record<string, unknown> {
	const defaults: Record<string, unknown> = {};
	if (schema?.properties) {
		for (const [k, p] of Object.entries(schema.properties)) {
			if (p.default !== undefined) defaults[k] = p.default;
		}
	}
	return defaults;
}

// ---------------------------------------------------------------------------
// Field components
// ---------------------------------------------------------------------------

interface FieldProps {
	id: string;
	label: string;
	required: boolean;
	prop: ParameterProperty;
	value: unknown;
	onChange: (next: unknown) => void;
}

function BooleanField({ id, label, prop, value, onChange }: FieldProps) {
	return (
		<>
			<Label htmlFor={id} className="whitespace-nowrap">
				{label}
			</Label>
			<Switch
				id={id}
				checked={value !== undefined ? !!value : !!prop.default}
				onCheckedChange={(checked) => onChange(!!checked)}
			/>
		</>
	);
}

function EnumField({
	id,
	label,
	required,
	prop,
	value,
	onChange,
	excludedValues = [],
}: FieldProps & { excludedValues?: unknown[] }) {
	const enumValues = (resolveType(prop).enumValues ?? []).filter(
		(option) => !excludedValues.includes(option),
	);
	// TODO: experiment with using prop.description as placeholder text
	const displayValue = value !== undefined && value !== null ? String(value) : '';

	return (
		<>
			<Label htmlFor={id} className="whitespace-nowrap">
				{label}
				{required && <span className="text-destructive ml-0.5">*</span>}
			</Label>
			<DropdownMenu>
				<DropdownMenuTrigger asChild>
					<Button id={id} variant="outline" className="w-full justify-between gap-1">
						<span className="truncate">{displayValue}</span>
						<ChevronDown className="size-3.5 opacity-50 shrink-0" />
					</Button>
				</DropdownMenuTrigger>
				<DropdownMenuContent
					align="start"
					className="max-h-60 overflow-y-auto"
					onPointerDown={(e) => e.stopPropagation()}
				>
					<DropdownMenuRadioGroup value={displayValue} onValueChange={(v) => onChange(v)}>
						{enumValues.map((opt) => (
							<DropdownMenuRadioItem key={String(opt)} value={String(opt)}>
								{String(opt)}
							</DropdownMenuRadioItem>
						))}
					</DropdownMenuRadioGroup>
				</DropdownMenuContent>
			</DropdownMenu>
		</>
	);
}

function NumberField({ id, label, required, prop, value, onChange }: FieldProps) {
	const { type: effectiveType } = resolveType(prop);

	return (
		<>
			<Label htmlFor={id} className="whitespace-nowrap">
				{label}
				{required && <span className="text-destructive ml-0.5">*</span>}
			</Label>
			<Input
				id={id}
				type="number"
				value={value !== undefined ? String(value) : ''}
				placeholder={prop.default != null ? String(prop.default) : undefined}
				min={prop.minimum}
				max={prop.maximum}
				step={effectiveType === 'number' ? 'any' : undefined}
				onChange={(e) => {
					const raw = e.target.value;
					onChange(raw === '' ? undefined : Number(raw));
				}}
				onBlur={(e) => {
					if (e.target.value === '') return;
					let num = Number(e.target.value);
					if (prop.minimum !== undefined && num < prop.minimum) num = prop.minimum;
					if (prop.maximum !== undefined && num > prop.maximum) num = prop.maximum;
					if (prop.exclusiveMinimum !== undefined && num <= prop.exclusiveMinimum)
						num =
							prop.exclusiveMinimum +
							(effectiveType === 'integer' ? 1 : Number.EPSILON);
					if (prop.exclusiveMaximum !== undefined && num >= prop.exclusiveMaximum)
						num =
							prop.exclusiveMaximum -
							(effectiveType === 'integer' ? 1 : Number.EPSILON);
					if (num !== Number(e.target.value)) onChange(num);
				}}
			/>
		</>
	);
}

function StringField({ id, label, required, prop, value, onChange }: FieldProps) {
	return (
		<>
			<Label htmlFor={id} className="whitespace-nowrap">
				{label}
				{required && <span className="text-destructive ml-0.5">*</span>}
			</Label>
			<Input
				id={id}
				type="text"
				value={value !== undefined ? String(value) : ''}
				placeholder={prop.default != null ? String(prop.default) : undefined}
				onChange={(e) => onChange(e.target.value)}
			/>
		</>
	);
}

function JsonField({ id, label, required, prop, value, onChange }: FieldProps) {
	const [draft, setDraft] = useState(value === undefined ? '' : JSON.stringify(value));
	const [invalid, setInvalid] = useState(false);

	useEffect(() => {
		setDraft(value === undefined ? '' : JSON.stringify(value));
		setInvalid(false);
	}, [value]);

	return (
		<>
			<Label htmlFor={id} className="whitespace-nowrap">
				{label}
				{required && <span className="text-destructive ml-0.5">*</span>}
			</Label>
			<Input
				id={id}
				type="text"
				value={draft}
				aria-invalid={invalid}
				placeholder={prop.default === undefined ? undefined : JSON.stringify(prop.default)}
				onChange={(event) => {
					setDraft(event.target.value);
					setInvalid(false);
				}}
				onBlur={() => {
					if (draft.trim() === '') {
						onChange(undefined);
						return;
					}
					try {
						const parsed = JSON.parse(draft) as unknown;
						const expected = resolveType(prop).type;
						const matches =
							(expected === 'array' && Array.isArray(parsed)) ||
							(expected === 'object' &&
								typeof parsed === 'object' &&
								parsed !== null &&
								!Array.isArray(parsed));
						if (!matches) {
							setInvalid(true);
							return;
						}
						onChange(parsed);
					} catch {
						setInvalid(true);
					}
				}}
			/>
		</>
	);
}

interface ASRModelMenuProps {
	groups: Record<string, CredentialWithASRModels[]>;
	selectedASRModel: ASRModelConfig | null;
	onASRChange: (config: ASRModelConfig | null) => void;
}

function ASRModelMenu({ groups, selectedASRModel, onASRChange }: ASRModelMenuProps) {
	const { t } = useTranslation();
	const groupEntries = Object.entries(groups)
		.map(([type, items]) => [type, items.filter((item) => item.models.length > 0)] as const)
		.filter(([, items]) => items.length > 0);

	let selectedCard: ASRModelCard | undefined;
	if (selectedASRModel) {
		for (const item of groups[selectedASRModel.type] ?? []) {
			if (item.credential.id !== selectedASRModel.credential_id) continue;
			selectedCard = item.models.find((model) => model.name === selectedASRModel.model);
			if (selectedCard) break;
		}
	}

	const renderModels = (type: string, credential: CredentialView, models: ASRModelCard[]) =>
		models.map((model) => {
			const selected =
				selectedASRModel?.credential_id === credential.id &&
				selectedASRModel.model === model.name;
			return (
				<DropdownMenuCheckboxItem
					key={`${credential.id}-${model.name}`}
					checked={selected}
					onSelect={(event) => event.preventDefault()}
					onCheckedChange={(checked) => {
						if (!checked) return;
						onASRChange({
							type,
							credential_id: credential.id,
							model: model.name,
							parameters: extractDefaults(
								model.parameter_schema as ParameterSchema | undefined,
							),
						});
					}}
				>
					<span className="min-w-0 flex-1 truncate">{model.label}</span>
					{model.realtime && (
						<Badge variant="outline" className="ml-1.5 shrink-0 px-1 py-0 text-[10px]">
							Realtime
						</Badge>
					)}
				</DropdownMenuCheckboxItem>
			);
		});

	return (
		<DropdownMenuSub>
			<DropdownMenuSubTrigger>
				<span className="truncate">{t('model-parameters.asrLabel')}</span>
			</DropdownMenuSubTrigger>
			<DropdownMenuSubContent className="max-h-96 w-64 max-w-[calc(100vw-2rem)] overflow-y-auto">
				{groupEntries.length === 0 ? (
					<div className="px-2 py-3 text-center text-sm text-muted-foreground">
						<p>{t('model-parameters.asrEmpty')}</p>
					</div>
				) : (
					groupEntries.map(([type, items], index) => {
						const singleCredential = items.length === 1;
						return (
							<DropdownMenuGroup key={type}>
								{index > 0 && <DropdownMenuSeparator />}
								<DropdownMenuLabel>
									{type.replace(/_credential$/, '')}
								</DropdownMenuLabel>
								{singleCredential
									? renderModels(type, items[0].credential, items[0].models)
									: items.map(({ credential, models }) => {
											const selected = models.some(
												(model) =>
													selectedASRModel?.credential_id ===
														credential.id &&
													selectedASRModel.model === model.name,
											);
											return (
												<DropdownMenuSub key={credential.id}>
													<DropdownMenuSubTrigger>
														<Check
															className={cn(
																'size-4 mr-1.5 shrink-0',
																!selected && 'invisible',
															)}
														/>
														{credentialLabel(credential)}
													</DropdownMenuSubTrigger>
													<DropdownMenuPortal>
														<DropdownMenuSubContent className="max-h-60 overflow-y-auto">
															{renderModels(type, credential, models)}
														</DropdownMenuSubContent>
													</DropdownMenuPortal>
												</DropdownMenuSub>
											);
										})}
							</DropdownMenuGroup>
						);
					})
				)}

				{selectedASRModel && selectedCard && (
					<>
						<DropdownMenuSeparator />
						<DropdownMenuSub>
							<DropdownMenuSubTrigger>
								{t('model-parameters.asrParameters')}
							</DropdownMenuSubTrigger>
							<DropdownMenuSubContent className="w-80 max-h-96 overflow-y-auto p-3">
								<p className="mb-3 text-xs text-muted-foreground">
									{t('model-parameters.asrParametersDescription')}
								</p>
								{(() => {
									const schema = selectedCard.parameter_schema as
										| ParameterSchema
										| undefined;
									const entries = Object.entries(schema?.properties ?? {});
									if (entries.length === 0) {
										return (
											<p className="text-xs text-muted-foreground">
												{t('model-parameters.empty')}
											</p>
										);
									}
									const required = schema?.required ?? [];
									const values = selectedASRModel.parameters ?? {};
									return (
										<div className="grid grid-cols-[auto_1fr] items-center gap-x-3 gap-y-3">
											{entries.map(([key, prop]) => {
												const fieldProps: FieldProps = {
													id: `asr-${selectedCard.name}-${key}`,
													label: prop.title ?? key,
													required: required.includes(key),
													prop,
													value: values[key],
													onChange: (value) => {
														const parameters = {
															...values,
															[key]: value,
														};
														if (value === '' || value === undefined)
															delete parameters[key];
														onASRChange({
															...selectedASRModel,
															parameters,
														});
													},
												};
												const { type, enumValues } = resolveType(prop);
												let field: React.ReactNode;
												if (type === 'boolean')
													field = <BooleanField {...fieldProps} />;
												else if (enumValues)
													field = <EnumField {...fieldProps} />;
												else if (type === 'number' || type === 'integer')
													field = <NumberField {...fieldProps} />;
												else if (type === 'array' || type === 'object')
													field = <JsonField {...fieldProps} />;
												else field = <StringField {...fieldProps} />;
												return (
													<Tooltip key={key}>
														<TooltipTrigger asChild>
															<div className="col-span-2 grid grid-cols-subgrid items-center">
																{field}
															</div>
														</TooltipTrigger>
														{prop.description && (
															<TooltipContent side="left">
																{prop.description}
															</TooltipContent>
														)}
													</Tooltip>
												);
											})}
										</div>
									);
								})()}
							</DropdownMenuSubContent>
						</DropdownMenuSub>
					</>
				)}

				<DropdownMenuSeparator />
				<DropdownMenuCheckboxItem
					checked={!selectedASRModel}
					onSelect={(event) => event.preventDefault()}
					onCheckedChange={(checked) => {
						if (checked) onASRChange(null);
					}}
				>
					{t('model-parameters.noAsr')}
				</DropdownMenuCheckboxItem>
			</DropdownMenuSubContent>
		</DropdownMenuSub>
	);
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

interface Props {
	/** Currently selected primary model — used to read the parameter schema. */
	selectedModel: ChatModelConfig | RealtimeModelConfig | null;
	/** Model card describing the primary model's parameter schema. */
	modelCard: ModelCard | RealtimeModelCard | null;
	/** Called when the user edits the primary model's parameters. */
	onChange: (parameters: Record<string, unknown>) => void;
	/** Currently selected fallback model. `null` means no fallback configured. `undefined` hides the section. */
	selectedFallbackModel?: ChatModelConfig | null;
	/** Called when the user picks a fallback model or clears the selection. */
	onFallbackChange?: (config: ChatModelConfig | null) => void;
	/** Currently selected TTS model. `null` means TTS is disabled. `undefined` hides the TTS section entirely. */
	selectedTTSModel?: TTSModelConfig | null;
	/** Called when the user picks a TTS model+voice or disables TTS. */
	onTTSChange?: (config: TTSModelConfig | null) => void;
	/** Currently selected ASR model. `null` means speech input is disabled. */
	selectedASRModel?: ASRModelConfig | null;
	/** Called when the user picks an ASR model or disables speech input. */
	onASRChange?: (config: ASRModelConfig | null) => void;
	/**
	 * Locks the trigger while a session config write is in flight, so a
	 * second edit cannot race the first. The trigger is also locked
	 * whenever no primary model is selected.
	 */
	disabled?: boolean;
	/** Prefix field ids when more than one parameter editor is rendered. */
	idPrefix?: string;
	/** Accessible label for the icon-only trigger. */
	triggerLabel?: string;
	/** Enum values unavailable in the current transport or UI. */
	excludedEnumValues?: Record<string, unknown[]>;
	/** Hide array fields when the caller has no array editor. */
	hideArrayParameters?: boolean;
}

/**
 * A unified settings dropdown for the active chat model. Exposes two
 * sub-menus:
 *   - "Fallback model": pick a backup model invoked when the primary fails.
 *   - "Parameters": edit the primary model's inference parameters inline.
 *
 * The trigger is disabled until a primary model is selected, since both
 * sub-menus are meaningless without one.
 */
export function ModelParametersPopover({
	selectedModel,
	modelCard,
	onChange,
	selectedFallbackModel,
	onFallbackChange,
	selectedTTSModel,
	onTTSChange,
	selectedASRModel,
	onASRChange,
	disabled: locked = false,
	idPrefix = 'model',
	triggerLabel,
	excludedEnumValues,
	hideArrayParameters = false,
}: Props) {
	const [values, setValues] = useState<Record<string, unknown>>({});
	const { t } = useTranslation();
	const { groups } = useAvailableModels();
	const { groups: ttsGroups } = useAvailableTTSModels();
	const { groups: asrGroups } = useAvailableASRModels();

	const schema = modelCard?.parameter_schema as ParameterSchema | undefined;
	const properties = schema?.properties ?? {};
	const required = schema?.required ?? [];
	const entries = Object.entries(properties).filter(
		([, prop]) => !hideArrayParameters || resolveType(prop).type !== 'array',
	);

	useEffect(() => {
		setValues(selectedModel?.parameters ?? {});
	}, [selectedModel]);

	const handleChange = useCallback(
		(key: string, value: unknown) => {
			const next = { ...values, [key]: value };
			if (value === '' || value === undefined) {
				delete next[key];
			}
			setValues(next);
			onChange(next);
		},
		[values, onChange],
	);

	const handleSelectFallback = (type: string, credentialId: string, model: string) => {
		onFallbackChange?.({
			type,
			credential_id: credentialId,
			model,
			parameters: {},
		});
	};

	const disabled = locked || !selectedModel;
	// Credentials whose model list failed to load come back with an empty
	// `models` array; drop them so they don't render empty submenus
	const fallbackGroups = Object.entries(groups)
		.map(([type, items]) => [type, items.filter((i) => i.models.length > 0)] as const)
		.filter(([, usable]) => usable.length > 0);
	const ttsGroupEntries = Object.entries(ttsGroups)
		.map(([type, items]) => [type, items.filter((i) => i.models.length > 0)] as const)
		.filter(([, usable]) => usable.length > 0);
	const hasFallbackOptions = fallbackGroups.length > 0;

	return (
		<DropdownMenu>
			<DropdownMenuTrigger asChild>
				<Button
					variant="ghost"
					size="icon-sm"
					disabled={disabled}
					aria-label={triggerLabel}
					title={triggerLabel}
				>
					<SlidersHorizontal />
				</Button>
			</DropdownMenuTrigger>
			<DropdownMenuContent align="start" className="min-w-40">
				{/* ----- Fallback model selection ----- */}
				{onFallbackChange !== undefined && (
					<DropdownMenuSub>
						<DropdownMenuSubTrigger>
							<span className="truncate">
								{selectedFallbackModel
									? t('model-parameters.fallbackLabelWithModel', {
											model: selectedFallbackModel.model,
										})
									: t('model-parameters.fallbackLabel')}
							</span>
						</DropdownMenuSubTrigger>
						<DropdownMenuSubContent className="max-h-72 overflow-y-auto">
							{!hasFallbackOptions ? (
								<div className="px-2 py-3 text-center text-sm text-muted-foreground">
									<p>{t('llm-select.empty.title')}</p>
								</div>
							) : (
								fallbackGroups.map(([type, usable], idx) => {
									const isSingle = usable.length === 1;

									const renderFallbackItems = (
										credential: CredentialView,
										models: ModelCard[],
									) =>
										models.map((m) => {
											const isSelected =
												selectedFallbackModel?.credential_id ===
													credential.id &&
												selectedFallbackModel?.model === m.name;
											return (
												<DropdownMenuCheckboxItem
													key={`${credential.id}-${m.name}`}
													checked={isSelected}
													onCheckedChange={(checked) => {
														if (checked) {
															handleSelectFallback(
																type,
																credential.id,
																m.name,
															);
														} else {
															onFallbackChange(null);
														}
													}}
												>
													{m.label}
												</DropdownMenuCheckboxItem>
											);
										});

									return (
										<DropdownMenuGroup key={type}>
											{idx > 0 && <DropdownMenuSeparator />}
											<DropdownMenuLabel>
												{type.replace(/_credential$/, '')}
											</DropdownMenuLabel>
											{isSingle
												? renderFallbackItems(
														usable[0].credential,
														usable[0].models,
													)
												: usable.map(({ credential, models }) => {
														const hasSelected = models.some(
															(m) =>
																selectedFallbackModel?.credential_id ===
																	credential.id &&
																selectedFallbackModel?.model ===
																	m.name,
														);
														return (
															<DropdownMenuSub key={credential.id}>
																<DropdownMenuSubTrigger>
																	<Check
																		className={cn(
																			'size-4 mr-1.5 shrink-0',
																			!hasSelected &&
																				'invisible',
																		)}
																	/>
																	{credentialLabel(credential)}
																</DropdownMenuSubTrigger>
																<DropdownMenuPortal>
																	<DropdownMenuSubContent className="max-h-60 overflow-y-auto">
																		{renderFallbackItems(
																			credential,
																			models,
																		)}
																	</DropdownMenuSubContent>
																</DropdownMenuPortal>
															</DropdownMenuSub>
														);
													})}
										</DropdownMenuGroup>
									);
								})
							)}
							<DropdownMenuSeparator />
							<DropdownMenuCheckboxItem
								checked={!selectedFallbackModel}
								onCheckedChange={(checked) => {
									if (checked) onFallbackChange(null);
								}}
							>
								{t('llm-select.noFallback')}
							</DropdownMenuCheckboxItem>
						</DropdownMenuSubContent>
					</DropdownMenuSub>
				)}

				{/* ----- Primary model parameters ----- */}
				<DropdownMenuSub>
					<DropdownMenuSubTrigger>
						{t('model-parameters.parametersLabel')}
					</DropdownMenuSubTrigger>
					<DropdownMenuPortal>
						<DropdownMenuSubContent className="w-80 max-h-(--radix-dropdown-menu-content-available-height) overflow-x-hidden overflow-y-auto p-3">
							<div className="mb-3">
								<p className="text-sm font-medium">{t('model-parameters.title')}</p>
								<p className="text-muted-foreground text-xs">
									{t('model-parameters.description')}
								</p>
							</div>
							{entries.length === 0 ? (
								<p className="text-muted-foreground text-xs">
									{t('model-parameters.empty')}
								</p>
							) : (
								<div
									className="grid grid-cols-[auto_1fr] items-center gap-x-3 gap-y-3"
									onPointerDown={(e) => e.stopPropagation()}
									onKeyDown={(e) => e.stopPropagation()}
								>
									{entries.map(([key, prop]) => {
										const { type: effectiveType, enumValues } =
											resolveType(prop);
										const label = prop.title ?? key;
										const isRequired = required.includes(key);
										const fieldProps: FieldProps = {
											id: `param-${idPrefix}-${key}`,
											label,
											required: isRequired,
											prop,
											value: values[key],
											onChange: (v) => handleChange(key, v),
										};

										let field: React.ReactNode;
										if (effectiveType === 'boolean') {
											field = <BooleanField {...fieldProps} />;
										} else if (enumValues) {
											field = (
												<EnumField
													{...fieldProps}
													excludedValues={excludedEnumValues?.[key]}
												/>
											);
										} else if (
											effectiveType === 'number' ||
											effectiveType === 'integer'
										) {
											field = <NumberField {...fieldProps} />;
										} else {
											field = <StringField {...fieldProps} />;
										}

										return (
											<Tooltip key={key}>
												<TooltipTrigger asChild>
													<div className="col-span-2 grid grid-cols-subgrid items-center">
														{field}
													</div>
												</TooltipTrigger>
												{prop.description && (
													<TooltipContent side="left">
														{prop.description}
													</TooltipContent>
												)}
											</Tooltip>
										);
									})}
								</div>
							)}
						</DropdownMenuSubContent>
					</DropdownMenuPortal>
				</DropdownMenuSub>

				{/* ----- TTS ----- */}
				{onTTSChange !== undefined && (
					<DropdownMenuSub>
						<DropdownMenuSubTrigger>
							<span className="truncate">{t('model-parameters.ttsLabel')}</span>
						</DropdownMenuSubTrigger>
						<DropdownMenuSubContent className="max-h-96 overflow-y-auto">
							{ttsGroupEntries.length === 0 ? (
								<div className="px-2 py-3 text-center text-sm text-muted-foreground">
									<p>{t('model-parameters.ttsEmpty')}</p>
								</div>
							) : (
								ttsGroupEntries.map(([type, usable], idx) => {
									const isSingle = usable.length === 1;

									const renderTTSItems = (
										credential: CredentialView,
										models: TTSModelCard[],
									) =>
										models.map((m) => {
											const isSelected =
												selectedTTSModel?.credential_id === credential.id &&
												selectedTTSModel?.model === m.name;
											return (
												<DropdownMenuCheckboxItem
													key={`${credential.id}-${m.name}`}
													checked={isSelected}
													onSelect={(e) => e.preventDefault()}
													onCheckedChange={(checked) => {
														if (!checked) return;
														onTTSChange({
															type,
															credential_id: credential.id,
															model: m.name,
															parameters: extractDefaults(
																m.parameter_schema as
																	| ParameterSchema
																	| undefined,
															),
														});
													}}
												>
													{m.label}
													{m.realtime && (
														<Badge
															variant="outline"
															className="ml-1.5 text-[10px] px-1 py-0"
														>
															Realtime
														</Badge>
													)}
												</DropdownMenuCheckboxItem>
											);
										});

									return (
										<DropdownMenuGroup key={type}>
											{idx > 0 && <DropdownMenuSeparator />}
											<DropdownMenuLabel>
												{type.replace(/_credential$/, '')}
											</DropdownMenuLabel>
											{isSingle
												? renderTTSItems(
														usable[0].credential,
														usable[0].models,
													)
												: usable.map(({ credential, models }) => {
														const hasSelected = models.some(
															(m) =>
																selectedTTSModel?.credential_id ===
																	credential.id &&
																selectedTTSModel?.model === m.name,
														);
														return (
															<DropdownMenuSub key={credential.id}>
																<DropdownMenuSubTrigger>
																	<Check
																		className={cn(
																			'size-4 mr-1.5 shrink-0',
																			!hasSelected &&
																				'invisible',
																		)}
																	/>
																	{credentialLabel(credential)}
																</DropdownMenuSubTrigger>
																<DropdownMenuPortal>
																	<DropdownMenuSubContent className="max-h-60 overflow-y-auto">
																		{renderTTSItems(
																			credential,
																			models,
																		)}
																	</DropdownMenuSubContent>
																</DropdownMenuPortal>
															</DropdownMenuSub>
														);
													})}
										</DropdownMenuGroup>
									);
								})
							)}

							{/* TTS parameters sub-panel (hover to expand right) */}
							{selectedTTSModel && (
								<>
									<DropdownMenuSeparator />
									<DropdownMenuSub>
										<DropdownMenuSubTrigger>
											{t('model-parameters.ttsParameters')}
										</DropdownMenuSubTrigger>
										<DropdownMenuSubContent className="w-72 max-h-96 overflow-y-auto p-3">
											<div className="mb-3">
												<p className="text-sm font-medium">
													{t('model-parameters.title')}
												</p>
												<p className="text-muted-foreground text-xs">
													{t('model-parameters.ttsParametersDescription')}
												</p>
											</div>
											{(() => {
												if (!selectedTTSModel) return null;
												const selType = selectedTTSModel.type;
												const selItems = ttsGroups[selType];
												if (!selItems) return null;
												let selModel: TTSModelCard | undefined;
												for (const { credential, models } of selItems) {
													if (
														credential.id !==
														selectedTTSModel.credential_id
													)
														continue;
													selModel = models.find(
														(m) => m.name === selectedTTSModel.model,
													);
													if (selModel) break;
												}
												if (!selModel) return null;
												const mSchema = selModel.parameter_schema as
													| ParameterSchema
													| undefined;
												const mProps = mSchema?.properties ?? {};
												const mRequired = mSchema?.required ?? [];
												const mEntries = Object.entries(mProps);
												if (mEntries.length === 0) {
													return (
														<p className="text-muted-foreground text-xs">
															{t('model-parameters.empty')}
														</p>
													);
												}
												const curParams = selectedTTSModel.parameters ?? {};

												return (
													<div
														className="grid grid-cols-[auto_1fr] items-center gap-x-3 gap-y-3"
														onPointerDown={(e) => e.stopPropagation()}
														onKeyDown={(e) => e.stopPropagation()}
													>
														{mEntries.map(([key, prop]) => {
															const {
																type: effectiveType,
																enumValues,
															} = resolveType(prop);
															const label = prop.title ?? key;
															const isReq = mRequired.includes(key);
															const fieldProps: FieldProps = {
																id: `tts-${selModel!.name}-${key}`,
																label,
																required: isReq,
																prop,
																value: curParams[key],
																onChange: (v) => {
																	const next = {
																		...curParams,
																		[key]: v,
																	};
																	if (
																		v === '' ||
																		v === undefined
																	) {
																		delete next[key];
																	}
																	onTTSChange({
																		...selectedTTSModel,
																		parameters: next,
																	});
																},
															};

															let field: React.ReactNode;
															if (effectiveType === 'boolean') {
																field = (
																	<BooleanField {...fieldProps} />
																);
															} else if (enumValues) {
																field = (
																	<EnumField {...fieldProps} />
																);
															} else if (
																effectiveType === 'number' ||
																effectiveType === 'integer'
															) {
																field = (
																	<NumberField {...fieldProps} />
																);
															} else {
																field = (
																	<StringField {...fieldProps} />
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
																		<TooltipContent side="left">
																			{prop.description}
																		</TooltipContent>
																	)}
																</Tooltip>
															);
														})}
													</div>
												);
											})()}
										</DropdownMenuSubContent>
									</DropdownMenuSub>
								</>
							)}

							<DropdownMenuSeparator />
							<DropdownMenuCheckboxItem
								checked={!selectedTTSModel}
								onSelect={(e) => e.preventDefault()}
								onCheckedChange={(checked) => {
									if (checked) onTTSChange(null);
								}}
							>
								{t('model-parameters.noTts')}
							</DropdownMenuCheckboxItem>
						</DropdownMenuSubContent>
					</DropdownMenuSub>
				)}

				{/* ----- ASR ----- */}
				{onASRChange !== undefined && (
					<ASRModelMenu
						groups={asrGroups}
						selectedASRModel={selectedASRModel ?? null}
						onASRChange={onASRChange}
					/>
				)}
			</DropdownMenuContent>
		</DropdownMenu>
	);
}
