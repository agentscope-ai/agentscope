import type { CredentialSchema } from '@/api';
import { Checkbox } from '@/components/ui/checkbox';
import { Field, FieldGroup, FieldLabel } from '@/components/ui/field.tsx';
import { Input } from '@/components/ui/input';

const SKIP_FIELDS = new Set(['id', 'type']);

interface Props {
	schema: CredentialSchema;
	values: Record<string, string | boolean>;
	onChange: (key: string, value: string | boolean) => void;
}

export function SchemaForm({ schema, values, onChange }: Props) {
	const entries = Object.entries(schema.properties).filter(
		([key, prop]) => !SKIP_FIELDS.has(key) && prop.const === undefined,
	);

	return (
		<FieldGroup>
			{entries.map(([key, prop]) => {
				const isRequired = schema.required?.includes(key) ?? false;
				const isPassword = prop.format === 'password';
				const effectiveType =
					prop.type ?? prop.anyOf?.find((t) => t.type !== 'null')?.type ?? 'string';
				const isBoolean = effectiveType === 'boolean';
				const label = prop.title ?? key.replace(/_/g, ' ');

				if (isBoolean) {
					return (
						<Field key={key}>
							<div className="flex items-center gap-x-2">
								<Checkbox
									id={key}
									checked={!!values[key]}
									onCheckedChange={(checked) => onChange(key, !!checked)}
								/>
								<FieldLabel htmlFor={key}>{label}</FieldLabel>
							</div>
						</Field>
					);
				}

				return (
					<Field key={key}>
						<FieldLabel htmlFor={key}>
							{label}
							{isRequired && <span className="text-destructive ml-0.5">*</span>}
						</FieldLabel>
						<Input
							id={key}
							type={
								isPassword
									? 'password'
									: effectiveType === 'number' || effectiveType === 'integer'
										? 'number'
										: 'text'
							}
							value={(values[key] as string) ?? ''}
							onChange={(e) => onChange(key, e.target.value)}
							placeholder={prop.description}
						/>
					</Field>
				);
			})}
		</FieldGroup>
	);
}
