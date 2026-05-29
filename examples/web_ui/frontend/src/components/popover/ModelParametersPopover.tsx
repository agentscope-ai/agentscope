import { SlidersHorizontal } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';

import type { ChatModelConfig, ModelCard } from '@/api';
import { Button } from '@/components/ui/button';
import {
	DropdownMenu,
	DropdownMenuCheckboxItem,
	DropdownMenuContent,
	DropdownMenuLabel,
	DropdownMenuSeparator,
	DropdownMenuSub,
	DropdownMenuSubContent,
	DropdownMenuSubTrigger,
	DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu.tsx';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { useAvailableModels } from '@/hooks/useAvailableModels';
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
	anyOf?: Array<{ type: string }>;
}

interface ParameterSchema {
	title?: string;
	description?: string;
	type?: string;
	properties?: Record<string, ParameterProperty>;
	required?: string[];
}

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
}: Props) {
	const [values, setValues] = useState<Record<string, unknown>>({});
	const { t } = useTranslation();
	const { groups } = useAvailableModels();

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
				{/* The sub-trigger reflects the current fallback selection so the
				    user can see the active model without drilling in. The label
				    is truncated with an ellipsis to keep the trigger on one line
				    when the model name is long. */}
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
											// Checkbox-style indicator: a check appears next
											// to the active fallback. Toggling off the active
											// item clears the selection (mirrors the explicit
											// "No fallback" entry below).
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
						{/* "No fallback" is checked exactly when no fallback is set,
						    making the list behave like a single-select group. Clicking
						    it while already checked is a no-op. */}
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
							<div className="grid grid-cols-[auto_1fr] items-center gap-x-3 gap-y-3">
								{entries.map(([key, prop]) => {
									const effectiveType =
										prop.type ??
										prop.anyOf?.find((t) => t.type !== 'null')?.type ??
										'string';
									const isBoolean = effectiveType === 'boolean';
									const isNumber =
										effectiveType === 'number' || effectiveType === 'integer';
									const label = prop.title ?? key;
									const isRequired = required.includes(key);

									if (isBoolean) {
										return (
											<div
												key={key}
												className="contents"
												// Keep clicks/keys inside the form from
												// closing the dropdown menu.
												onPointerDown={(e) => e.stopPropagation()}
												onKeyDown={(e) => e.stopPropagation()}
											>
												<Label
													htmlFor={`param-${key}`}
													className="whitespace-nowrap"
												>
													{label}
												</Label>
												<Switch
													id={`param-${key}`}
													checked={
														values[key] !== undefined
															? !!values[key]
															: !!prop.default
													}
													onCheckedChange={(checked) =>
														handleChange(key, !!checked)
													}
												/>
											</div>
										);
									}

									return (
										<div
											key={key}
											className="contents"
											onPointerDown={(e) => e.stopPropagation()}
											onKeyDown={(e) => e.stopPropagation()}
										>
											<Label
												htmlFor={`param-${key}`}
												className="whitespace-nowrap"
											>
												{label}
												{isRequired && (
													<span className="text-destructive ml-0.5">
														*
													</span>
												)}
											</Label>
											<Input
												id={`param-${key}`}
												type={isNumber ? 'number' : 'text'}
												value={
													values[key] !== undefined
														? String(values[key])
														: ''
												}
												placeholder={
													prop.default !== undefined
														? String(prop.default)
														: undefined
												}
												min={prop.minimum}
												max={prop.maximum}
												step={
													isNumber && effectiveType === 'number'
														? 'any'
														: undefined
												}
												onChange={(e) => {
													const raw = e.target.value;
													if (isNumber) {
														handleChange(
															key,
															raw === '' ? undefined : Number(raw),
														);
													} else {
														handleChange(key, raw);
													}
												}}
												onBlur={(e) => {
													if (!isNumber || e.target.value === '') return;
													let num = Number(e.target.value);
													if (
														prop.minimum !== undefined &&
														num < prop.minimum
													)
														num = prop.minimum;
													if (
														prop.maximum !== undefined &&
														num > prop.maximum
													)
														num = prop.maximum;
													if (
														prop.exclusiveMinimum !== undefined &&
														num <= prop.exclusiveMinimum
													)
														num =
															prop.exclusiveMinimum +
															(effectiveType === 'integer'
																? 1
																: Number.EPSILON);
													if (
														prop.exclusiveMaximum !== undefined &&
														num >= prop.exclusiveMaximum
													)
														num =
															prop.exclusiveMaximum -
															(effectiveType === 'integer'
																? 1
																: Number.EPSILON);
													if (num !== Number(e.target.value))
														handleChange(key, num);
												}}
											/>
										</div>
									);
								})}
							</div>
						)}
					</DropdownMenuSubContent>
				</DropdownMenuSub>
			</DropdownMenuContent>
		</DropdownMenu>
	);
}
