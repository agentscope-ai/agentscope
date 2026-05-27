import { useTranslation } from 'react-i18next';

import type { AgentSchemaResponse } from '@/api';
import { SchemaForm, type SchemaFormValue } from '@/components/form/SchemaForm';
import {
	FieldDescription,
	FieldGroup,
	FieldLegend,
	FieldSeparator,
	FieldSet,
} from '@/components/ui/field';

export type AgentSection = 'identity' | 'context_config' | 'react_config';

export type AgentFormValues = {
	[K in AgentSection]: Record<string, SchemaFormValue>;
};

interface Props {
	schema: AgentSchemaResponse;
	values: AgentFormValues;
	onChange: (section: AgentSection, key: string, value: SchemaFormValue) => void;
}

const SECTIONS: { key: AgentSection; i18n: string }[] = [
	{ key: 'identity', i18n: 'identity' },
	{ key: 'context_config', i18n: 'context-config' },
	{ key: 'react_config', i18n: 'react-config' },
];

const toKebab = (s: string) => s.replace(/_/g, '-');

export function AgentFormFields({ schema, values, onChange }: Props) {
	const { t } = useTranslation();

	return (
		<FieldGroup>
			{SECTIONS.map(({ key: sectionKey, i18n: sectionI18n }, idx) => {
				const sectionSchema = schema[sectionKey];
				const legend = t(`agent-form.${sectionI18n}.legend`, {
					defaultValue: sectionSchema.title ?? sectionKey,
				});
				const description = t(`agent-form.${sectionI18n}.description`, {
					defaultValue: '',
				});
				return (
					<div key={sectionKey}>
						{idx > 0 && <FieldSeparator className="my-0" />}
						<FieldSet>
							<FieldLegend>{legend}</FieldLegend>
							{description && <FieldDescription>{description}</FieldDescription>}
							<SchemaForm
								schema={sectionSchema}
								values={values[sectionKey]}
								onChange={(k, v) => onChange(sectionKey, k, v)}
								idPrefix={`agent-form-${sectionI18n}`}
								labelFor={(k, prop) =>
									t(`agent-form.${sectionI18n}.${toKebab(k)}.label`, {
										defaultValue: prop.title ?? k.replace(/_/g, ' '),
									})
								}
								placeholderFor={(k, prop) =>
									t(`agent-form.${sectionI18n}.${toKebab(k)}.placeholder`, {
										defaultValue: prop.description ?? '',
									}) || undefined
								}
							/>
						</FieldSet>
					</div>
				);
			})}
		</FieldGroup>
	);
}

/** Build a fresh `AgentFormValues` populated from each section schema's defaults. */
export function defaultAgentFormValues(schema: AgentSchemaResponse): AgentFormValues {
	const fromDefaults = (
		section: AgentSchemaResponse[AgentSection],
	): Record<string, SchemaFormValue> => {
		const out: Record<string, SchemaFormValue> = {};
		for (const [k, prop] of Object.entries(section.properties ?? {})) {
			if (prop.const !== undefined) continue;
			if (prop.default !== undefined) out[k] = prop.default as SchemaFormValue;
		}
		return out;
	};
	return {
		identity: fromDefaults(schema.identity),
		context_config: fromDefaults(schema.context_config),
		react_config: fromDefaults(schema.react_config),
	};
}
