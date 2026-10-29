import { Plus, Trash2 } from 'lucide-react';
import type { ReactNode } from 'react';

import type { JSONSchema, JSONSchemaProperty } from '@/api';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import {
	Field,
	FieldContent,
	FieldDescription,
	FieldGroup,
	FieldLabel,
} from '@/components/ui/field.tsx';
import { Input } from '@/components/ui/input';
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { useTranslation } from '@/i18n/useI18n.ts';

const DEFAULT_SKIP_FIELDS = new Set(['id', 'type']);

export type SchemaFormValue = string | number | boolean | string[] | null | undefined;

interface Props {
	schema: JSONSchema;
	values: Record<string, SchemaFormValue>;
	onChange: (key: string, value: SchemaFormValue) => void;
	/** Field names to omit from rendering. Defaults to `id` and `type`. */
	skipFields?: ReadonlySet<string>;
	/** Optional override for a field label. Falls back to `prop.title`. */
	labelFor?: (key: string, prop: JSONSchemaProperty) => string | undefined;
	/** Optional override for a field's placeholder/description. */
	placeholderFor?: (key: string, prop: JSONSchemaProperty) => string | undefined;
	/** Optional override for the helper text shown under a field. */
	descriptionFor?: (key: string, prop: JSONSchemaProperty) => string | undefined;
	/** Prefix for generated DOM IDs (so multiple SchemaForms on one page don't collide). */
	idPrefix?: string;
	/** Layout of non-boolean fields. `horizontal` puts the label left of the control. */
	orientation?: 'vertical' | 'horizontal';
	/** Extra classes for the wrapping FieldGroup. */
	className?: string;
}

function effectiveType(prop: JSONSchemaProperty): string {
	return prop.type ?? prop.anyOf?.find((t) => t.type !== 'null')?.type ?? 'string';
}

/** Surface enum values whether they appear at the property root or inside an
 *  `anyOf` variant (Pydantic puts `Literal[...]` directly on the property and
 *  `Literal[...] | None` on the non-null `anyOf` branch). */
function enumValues(prop: JSONSchemaProperty): unknown[] | null {
	if (prop.enum) return prop.enum;
	for (const variant of prop.anyOf ?? []) {
		const v = (variant as JSONSchemaProperty).enum;
		if (v) return v;
	}
	return null;
}

function inferStep(type: string): number | string | undefined {
	if (type === 'integer') return 1;
	if (type === 'number') return 'any';
	return undefined;
}

/** Render control characters as editable, single-line escape sequences. */
function displayStringArrayItem(value: string): string {
	return value.replace(/\r/g, '\\r').replace(/\n/g, '\\n').replace(/\t/g, '\\t');
}

/** Restore supported control-character escapes before schema submission. */
function parseStringArrayItem(value: string): string {
	return value.replace(/\\r/g, '\r').replace(/\\n/g, '\n').replace(/\\t/g, '\t');
}

/** Extract initial values from a JSON Schema's `default` fields. */
export function defaultValuesFromSchema(
	schema: JSONSchema,
	skipFields: ReadonlySet<string> = DEFAULT_SKIP_FIELDS,
): Record<string, SchemaFormValue> {
	const out: Record<string, SchemaFormValue> = {};
	for (const [key, prop] of Object.entries(schema.properties ?? {})) {
		if (skipFields.has(key) || prop.const !== undefined) continue;
		if (prop.default !== undefined) {
			out[key] = prop.default as SchemaFormValue;
		}
	}
	return out;
}

export function SchemaForm({
	schema,
	values,
	onChange,
	skipFields = DEFAULT_SKIP_FIELDS,
	labelFor,
	placeholderFor,
	descriptionFor,
	idPrefix = 'schema-form',
	orientation = 'vertical',
	className,
}: Props) {
	const { t } = useTranslation();
	const entries = Object.entries(schema.properties ?? {}).filter(
		([key, prop]) => !skipFields.has(key) && prop.const === undefined,
	);

	return (
		<FieldGroup className={className}>
			{entries.map(([key, prop]) => {
				const fieldId = `${idPrefix}-${key}`;
				const isRequired = schema.required?.includes(key) ?? false;
				const type = effectiveType(prop);
				const isBoolean = type === 'boolean';
				const isPassword = prop.format === 'password';
				const isTextarea = prop.format === 'textarea';
				const isNumber = type === 'number' || type === 'integer';
				const isStringArray =
					type === 'array' && prop.items && effectiveType(prop.items) === 'string';
				const enumOpts = enumValues(prop);

				const label = labelFor?.(key, prop) ?? prop.title ?? key.replace(/_/g, ' ');
				const placeholder = placeholderFor?.(key, prop) ?? prop.description;
				const description = descriptionFor?.(key, prop);
				const current = values[key];

				const labelNode = (
					<FieldLabel htmlFor={fieldId}>
						{label}
						{isRequired && <span className="text-destructive ml-0.5">*</span>}
					</FieldLabel>
				);
				const descriptionNode = description ? (
					<FieldDescription>{description}</FieldDescription>
				) : null;
				/** Label (+ helper text) left of the control when horizontal. */
				const wrap = (control: ReactNode) =>
					orientation === 'horizontal' ? (
						<Field key={key} orientation="horizontal">
							<FieldContent>
								{labelNode}
								{descriptionNode}
							</FieldContent>
							{control}
						</Field>
					) : (
						<Field key={key}>
							{labelNode}
							{control}
							{descriptionNode}
						</Field>
					);

				if (isBoolean) {
					const boxLabel = (
						<FieldLabel htmlFor={fieldId} className="font-normal">
							{label}
						</FieldLabel>
					);
					return (
						<Field key={key} orientation="horizontal">
							<Checkbox
								id={fieldId}
								checked={!!current}
								onCheckedChange={(checked) => onChange(key, !!checked)}
							/>
							{/* A described checkbox stacks its label and helper
							    text in a column beside the box; without one the
							    label stays inline, so a plain toggle keeps the
							    single centred row it has always had. */}
							{descriptionNode ? (
								<FieldContent>
									{boxLabel}
									{descriptionNode}
								</FieldContent>
							) : (
								boxLabel
							)}
						</Field>
					);
				}

				if (enumOpts) {
					const currentStr =
						current === undefined || current === null ? '' : String(current);
					return wrap(
						<Select value={currentStr} onValueChange={(v) => onChange(key, v)}>
							<SelectTrigger id={fieldId} className="w-full">
								<SelectValue placeholder={placeholder} />
							</SelectTrigger>
							<SelectContent>
								{enumOpts.map((opt) => (
									<SelectItem key={String(opt)} value={String(opt)}>
										{String(opt)}
									</SelectItem>
								))}
							</SelectContent>
						</Select>,
					);
				}

				if (isTextarea) {
					return wrap(
						<Textarea
							id={fieldId}
							rows={3}
							value={(current as string | undefined) ?? ''}
							onChange={(e) => onChange(key, e.target.value)}
							placeholder={placeholder}
						/>,
					);
				}

				if (isNumber) {
					const min = prop.minimum ?? prop.exclusiveMinimum;
					const max = prop.maximum ?? prop.exclusiveMaximum;
					return wrap(
						<Input
							id={fieldId}
							type="number"
							min={min}
							max={max}
							step={inferStep(type)}
							value={current === undefined || current === null ? '' : String(current)}
							onChange={(e) => {
								const raw = e.target.value;
								// Empty input → undefined so JSON.stringify drops the key
								// and the backend applies its own default. Sending "" would
								// fail Pydantic float/int coercion.
								if (raw === '') {
									onChange(key, undefined);
									return;
								}
								const parsed =
									type === 'integer' ? parseInt(raw, 10) : parseFloat(raw);
								onChange(key, Number.isNaN(parsed) ? raw : parsed);
							}}
							placeholder={placeholder}
						/>,
					);
				}

				if (isStringArray) {
					const items = Array.isArray(current)
						? current.filter((item): item is string => typeof item === 'string')
						: [];
					const minimumItems = prop.minItems ?? 0;
					const maximumItems = prop.maxItems;

					return wrap(
						<div className="space-y-1.5">
							<div className="max-h-44 space-y-1.5 overflow-y-auto pr-1">
								{items.map((item, index) => (
									<div key={index} className="flex items-center gap-1.5">
										<Input
											id={index === 0 ? fieldId : `${fieldId}-${index}`}
											className="font-mono"
											value={displayStringArrayItem(item)}
											placeholder={'""'}
											onChange={(event) => {
												const next = [...items];
												next[index] = parseStringArrayItem(
													event.target.value,
												);
												onChange(key, next);
											}}
										/>
										<Button
											type="button"
											variant="ghost"
											size="icon-sm"
											className="text-muted-foreground hover:text-destructive"
											disabled={items.length <= minimumItems}
											aria-label={t('schema-form.array.remove', {
												index: index + 1,
											})}
											tooltip={t('schema-form.array.remove', {
												index: index + 1,
											})}
											onClick={() => {
												onChange(
													key,
													items.filter(
														(_, itemIndex) => itemIndex !== index,
													),
												);
											}}
										>
											<Trash2 />
										</Button>
									</div>
								))}
							</div>
							<Button
								type="button"
								variant="outline"
								size="sm"
								className="w-full border-dashed text-muted-foreground"
								disabled={
									maximumItems !== undefined && items.length >= maximumItems
								}
								onClick={() => onChange(key, [...items, ''])}
							>
								<Plus />
								{t('schema-form.array.add')}
							</Button>
						</div>,
					);
				}

				return wrap(
					<Input
						id={fieldId}
						type={isPassword ? 'password' : 'text'}
						value={(current as string | undefined) ?? ''}
						onChange={(e) => onChange(key, e.target.value)}
						placeholder={placeholder}
					/>,
				);
			})}
		</FieldGroup>
	);
}
