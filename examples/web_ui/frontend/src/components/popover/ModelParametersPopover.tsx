import { SlidersHorizontal } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';

import type { ChatModelConfig, ModelCard } from '@/api';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
	Popover,
	PopoverContent,
	PopoverDescription,
	PopoverHeader,
	PopoverTitle,
	PopoverTrigger,
} from '@/components/ui/popover';
import { Switch } from '@/components/ui/switch';
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
	selectedModel: ChatModelConfig | null;
	modelCard: ModelCard | null;
	onChange: (parameters: Record<string, unknown>) => void;
}

export function ModelParametersPopover({ selectedModel, modelCard, onChange }: Props) {
	const [values, setValues] = useState<Record<string, unknown>>({});
	const { t } = useTranslation();

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

	const disabled = !selectedModel;

	return (
		<Popover>
			<PopoverTrigger asChild>
				<Button variant="ghost" size="icon-sm" disabled={disabled}>
					<SlidersHorizontal className="size-4" />
				</Button>
			</PopoverTrigger>
			<PopoverContent align="start" className="w-80 max-h-96 overflow-y-auto">
				<PopoverHeader>
					<PopoverTitle>{t('model-parameters.title')}</PopoverTitle>
					<PopoverDescription>{t('model-parameters.description')}</PopoverDescription>
				</PopoverHeader>
				{entries.length === 0 ? (
					<p className="text-muted-foreground text-xs">{t('model-parameters.empty')}</p>
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
									<>
										<Label
											key={`${key}-label`}
											htmlFor={`param-${key}`}
											className="whitespace-nowrap"
										>
											{label}
										</Label>
										<Switch
											key={`${key}-input`}
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
									</>
								);
							}

							return (
								<>
									<Label
										key={`${key}-label`}
										htmlFor={`param-${key}`}
										className="whitespace-nowrap"
									>
										{label}
										{isRequired && (
											<span className="text-destructive ml-0.5">*</span>
										)}
									</Label>
									<Input
										key={`${key}-input`}
										id={`param-${key}`}
										type={isNumber ? 'number' : 'text'}
										value={values[key] !== undefined ? String(values[key]) : ''}
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
											if (prop.minimum !== undefined && num < prop.minimum)
												num = prop.minimum;
											if (prop.maximum !== undefined && num > prop.maximum)
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
								</>
							);
						})}
					</div>
				)}
			</PopoverContent>
		</Popover>
	);
}
