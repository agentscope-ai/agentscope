import { useTranslation } from 'react-i18next';

import type { AgentSchemaV2Response, JSONSchema, JSONSchemaProperty } from '@/api';
import { SchemaForm, type SchemaFormValue } from '@/components/form/SchemaForm';
import {
	FieldDescription,
	FieldGroup,
	FieldLegend,
	FieldSeparator,
	FieldSet,
} from '@/components/ui/field';

export type AgentSection = 'identity' | 'context_config' | 'react_config' | 'invite_config';

export type AgentFormValues = {
	[K in AgentSection]: Record<string, SchemaFormValue>;
};

interface Props {
	schema: AgentSchemaV2Response;
	values: AgentFormValues;
	onChange: (section: AgentSection, key: string, value: SchemaFormValue) => void;
}

/**
 * Section derivation from the flat `AgentData` schema. Ordered — controls
 * the visual order of the fieldsets. "identity" carries every top-level
 * property that is NOT one of the nested-object sections below, so any
 * newly added scalar / boolean / textarea field on `AgentData` shows up
 * in the identity fieldset automatically.
 */
const NESTED_SECTIONS: Array<{ key: Exclude<AgentSection, 'identity'>; i18n: string }> = [
	{ key: 'context_config', i18n: 'context-config' },
	{ key: 'react_config', i18n: 'react-config' },
	{ key: 'invite_config', i18n: 'invite-config' },
];

const IDENTITY_I18N = 'identity';

const toKebab = (s: string) => s.replace(/_/g, '-');

/** Split the flat `AgentData` schema into the sections the form renders
 * (currently four: `identity` + one per `NESTED_SECTIONS` entry). */
function sliceSchema(root: JSONSchema): Record<AgentSection, JSONSchema> {
	const props = root.properties ?? {};
	const nestedKeys = new Set(NESTED_SECTIONS.map((s) => s.key));

	const identityProps: Record<string, JSONSchemaProperty> = {};
	for (const [k, prop] of Object.entries(props)) {
		if (nestedKeys.has(k as Exclude<AgentSection, 'identity'>)) continue;
		identityProps[k] = prop;
	}

	const identity: JSONSchema = {
		type: 'object',
		title: 'Identity',
		properties: identityProps,
		required: (root.required ?? []).filter(
			(r) => !nestedKeys.has(r as Exclude<AgentSection, 'identity'>),
		),
	};

	return {
		identity,
		context_config: (props.context_config as JSONSchema) ?? {
			type: 'object',
			properties: {},
		},
		react_config: (props.react_config as JSONSchema) ?? {
			type: 'object',
			properties: {},
		},
		invite_config: (props.invite_config as JSONSchema) ?? {
			type: 'object',
			properties: {},
		},
	};
}

export function AgentFormFields({ schema, values, onChange }: Props) {
	const { t } = useTranslation();
	const sections = sliceSchema(schema.schema);

	const rows: Array<{ key: AgentSection; i18n: string; sectionSchema: JSONSchema }> = [
		{ key: 'identity', i18n: IDENTITY_I18N, sectionSchema: sections.identity },
		...NESTED_SECTIONS.map((s) => ({
			key: s.key as AgentSection,
			i18n: s.i18n,
			sectionSchema: sections[s.key],
		})),
	];

	return (
		<FieldGroup>
			{rows.map(({ key: sectionKey, i18n: sectionI18n, sectionSchema }, idx) => {
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
export function defaultAgentFormValues(schema: AgentSchemaV2Response): AgentFormValues {
	const sections = sliceSchema(schema.schema);
	const fromDefaults = (section: JSONSchema): Record<string, SchemaFormValue> => {
		const out: Record<string, SchemaFormValue> = {};
		for (const [k, prop] of Object.entries(section.properties ?? {})) {
			if (prop.const !== undefined) continue;
			if (prop.default !== undefined) out[k] = prop.default as SchemaFormValue;
		}
		return out;
	};
	return {
		identity: fromDefaults(sections.identity),
		context_config: fromDefaults(sections.context_config),
		react_config: fromDefaults(sections.react_config),
		invite_config: fromDefaults(sections.invite_config),
	};
}
