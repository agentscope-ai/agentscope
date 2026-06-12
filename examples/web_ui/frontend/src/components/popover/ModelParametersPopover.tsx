import { ChevronDown, SlidersHorizontal } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';

import type { ChatModelConfig, ModelCard, TTSModelCard, TTSModelConfig } from '@/api';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
	DropdownMenu,
	DropdownMenuCheckboxItem,
	DropdownMenuContent,
	DropdownMenuLabel,
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
import { useAvailableModels } from '@/hooks/useAvailableModels';
import { useAvailableTTSModels } from '@/hooks/useAvailableTTSModels';
import { useTranslation } from '@/i18n/useI18n';

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

function EnumField({ id, label, required, prop, value, onChange }: FieldProps) {
	const enumValues = resolveType(prop).enumValues ?? [];
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

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

interface Props {
	/** Currently selected primary model — used to read the parameter schema. */
	selectedModel: ChatModelConfig | null;
	/** Model card describing the primary model's parameter schema. */
	modelCard: ModelCard | null;
	/** Called when the user edits the primary model's parameters. */
	onChange: (parameters: Record<string, unknown>) => void;
	/** Currently selected fallback model. `null` means no fallback configured. */
	selectedFallbackModel: ChatModelConfig | null;
	/** Called when the user picks a fallback model or clears the selection. */
	onFallbackChange: (config: ChatModelConfig | null) => void;
	/** Currently selected TTS model. `null` means TTS is disabled. */
	selectedTTSModel: TTSModelConfig | null;
	/** Called when the user picks a TTS model+voice or disables TTS. */
	onTTSChange: (config: TTSModelConfig | null) => void;
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
}: Props) {
	const [values, setValues] = useState<Record<string, unknown>>({});
	const { t } = useTranslation();
	const { groups } = useAvailableModels();
	const { groups: ttsGroups } = useAvailableTTSModels();

	const schema = modelCard?.parameter_schema as ParameterSchema | undefined;
	const properties = schema?.properties ?? {};
	const required = schema?.required ?? [];
	const entries = Object.entries(properties);

	useEffect(() => {
		setValues(selectedModel?.parameters ?? {});
	}, [selectedModel?.model]);

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
		onFallbackChange({
			type,
			credential_id: credentialId,
			model,
			parameters: {},
		});
	};

	const disabled = !selectedModel;
	const hasFallbackOptions = Object.keys(groups).length > 0;

	return (
		<DropdownMenu>
			<DropdownMenuTrigger asChild>
				<Button variant="ghost" size="icon-sm" disabled={disabled}>
					<SlidersHorizontal />
				</Button>
			</DropdownMenuTrigger>
			<DropdownMenuContent align="start" className="min-w-40">
				{/* ----- Fallback model selection ----- */}
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
							Object.entries(groups).map(([type, items], idx) => (
								<div key={type}>
									{idx > 0 && <DropdownMenuSeparator />}
									<DropdownMenuLabel>
										{type.replace(/_credential$/, '')}
									</DropdownMenuLabel>
									{items.flatMap(({ credential, models }) =>
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
										}),
									)}
								</div>
							))
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

				{/* ----- Primary model parameters ----- */}
				<DropdownMenuSub>
					<DropdownMenuSubTrigger>
						{t('model-parameters.parametersLabel')}
					</DropdownMenuSubTrigger>
					<DropdownMenuSubContent className="w-80 max-h-96 overflow-y-auto p-3">
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
									const { type: effectiveType, enumValues } = resolveType(prop);
									const label = prop.title ?? key;
									const isRequired = required.includes(key);
									const fieldProps: FieldProps = {
										id: `param-${key}`,
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
										field = <EnumField {...fieldProps} />;
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
				</DropdownMenuSub>

				{/* ----- TTS ----- */}
				<DropdownMenuSub>
					<DropdownMenuSubTrigger>
						<span className="truncate">{t('model-parameters.ttsLabel')}</span>
					</DropdownMenuSubTrigger>
					<DropdownMenuSubContent className="max-h-96 overflow-y-auto">
						{Object.keys(ttsGroups).length === 0 ? (
							<div className="px-2 py-3 text-center text-sm text-muted-foreground">
								<p>{t('model-parameters.ttsEmpty')}</p>
							</div>
						) : (
							Object.entries(ttsGroups).map(([type, items], idx) => (
								<div key={type}>
									{idx > 0 && <DropdownMenuSeparator />}
									<DropdownMenuLabel>
										{type.replace(/_credential$/, '')}
									</DropdownMenuLabel>
									{items.flatMap(({ credential, models }) =>
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
														const schema = m.parameter_schema as
															| ParameterSchema
															| undefined;
														const defaults: Record<string, unknown> =
															{};
														if (schema?.properties) {
															for (const [k, p] of Object.entries(
																schema.properties,
															)) {
																if (p.default !== undefined) {
																	defaults[k] = p.default;
																}
															}
														}
														onTTSChange({
															type,
															credential_id: credential.id,
															model: m.name,
															parameters: defaults,
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
										}),
									)}
								</div>
							))
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
													credential.id !== selectedTTSModel.credential_id
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
														const { type: effectiveType, enumValues } =
															resolveType(prop);
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
																if (v === '' || v === undefined) {
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
															field = <EnumField {...fieldProps} />;
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
			</DropdownMenuContent>
		</DropdownMenu>
	);
}
