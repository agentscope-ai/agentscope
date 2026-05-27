import type { JSONSchema, JSONSchemaProperty } from '@/api';
import { Checkbox } from '@/components/ui/checkbox';
import { Field, FieldDescription, FieldGroup, FieldLabel } from '@/components/ui/field.tsx';
import { Input } from '@/components/ui/input';
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
}

function effectiveType(prop: JSONSchemaProperty): string {
	return prop.type ?? prop.anyOf?.find((t) => t.type !== 'null')?.type ?? 'string';
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
}: Props) {
	const entries = Object.entries(schema.properties ?? {}).filter(
		([key, prop]) => !skipFields.has(key) && prop.const === undefined,
	);

	return (
		<FieldGroup>
			{entries.map(([key, prop]) => {
				const fieldId = `${idPrefix}-${key}`;
				const isRequired = schema.required?.includes(key) ?? false;
				const type = effectiveType(prop);
				const isBoolean = type === 'boolean';
				const isPassword = prop.format === 'password';
				const isTextarea = prop.format === 'textarea';
				const isNumber = type === 'number' || type === 'integer';

				const label = labelFor?.(key, prop) ?? prop.title ?? key.replace(/_/g, ' ');
				const placeholder = placeholderFor?.(key, prop) ?? prop.description;
				const description = descriptionFor?.(key, prop);
				const current = values[key];

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

				if (isTextarea) {
					return (
						<Field key={key}>
							<FieldLabel htmlFor={fieldId}>
								{label}
								{isRequired && <span className="text-destructive ml-0.5">*</span>}
							</FieldLabel>
							<Textarea
								id={fieldId}
								rows={3}
								value={(current as string | undefined) ?? ''}
								onChange={(e) => onChange(key, e.target.value)}
								placeholder={placeholder}
							/>
							{description && <FieldDescription>{description}</FieldDescription>}
						</Field>
					);
				}

				if (isNumber) {
					const min = prop.minimum ?? prop.exclusiveMinimum;
					const max = prop.maximum ?? prop.exclusiveMaximum;
					return (
						<Field key={key}>
							<FieldLabel htmlFor={fieldId}>
								{label}
								{isRequired && <span className="text-destructive ml-0.5">*</span>}
							</FieldLabel>
							<Input
								id={fieldId}
								type="number"
								min={min}
								max={max}
								step={inferStep(type)}
								value={
									current === undefined || current === null ? '' : String(current)
								}
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
							/>
							{description && <FieldDescription>{description}</FieldDescription>}
						</Field>
					);
				}

				return (
					<Field key={key}>
						<FieldLabel htmlFor={fieldId}>
							{label}
							{isRequired && <span className="text-destructive ml-0.5">*</span>}
						</FieldLabel>
						<Input
							id={fieldId}
							type={isPassword ? 'password' : 'text'}
							value={(current as string | undefined) ?? ''}
							onChange={(e) => onChange(key, e.target.value)}
							placeholder={placeholder}
						/>
						{description && <FieldDescription>{description}</FieldDescription>}
					</Field>
				);
			})}
		</FieldGroup>
	);
}
