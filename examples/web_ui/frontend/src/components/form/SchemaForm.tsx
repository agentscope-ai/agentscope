import type { ReactNode } from 'react';

import type { JSONSchema, JSONSchemaProperty } from '@/api';
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

const DEFAULT_SKIP_FIELDS = new Set(['id', 'type']);

export type SchemaFormValue = string | number | boolean | null | undefined;

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
					return (
						<Field key={key} orientation="horizontal">
							<Checkbox
								id={fieldId}
								checked={!!current}
								onCheckedChange={(checked) => onChange(key, !!checked)}
							/>
							<FieldLabel htmlFor={fieldId} className="font-normal">
								{label}
							</FieldLabel>
							{description && <FieldDescription>{description}</FieldDescription>}
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
