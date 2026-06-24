import { MessageSquareText, AudioLines } from 'lucide-react';
import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';

import type { AgentSchemaResponse, AgentType } from '@/api';
import { SchemaForm, type SchemaFormValue } from '@/components/form/SchemaForm';
import { Button } from '@/components/ui/button';
import {
	FieldDescription,
	FieldGroup,
	FieldLegend,
	FieldSeparator,
	FieldSet,
} from '@/components/ui/field';
import { cn } from '@/lib/utils';

export type AgentSection = 'identity' | 'context_config' | 'react_config';

export type AgentFormValues = {
	[K in AgentSection]: Record<string, SchemaFormValue>;
};

interface Props {
	schema: AgentSchemaResponse;
	values: AgentFormValues;
	onChange: (section: AgentSection, key: string, value: SchemaFormValue) => void;
	/** Lock the agent type selector (e.g. when editing an existing agent). */
	lockType?: boolean;
}

const IDENTITY_SKIP_FIELDS = new Set(['id', 'type', 'agent_type']);

const SECTIONS: { key: AgentSection; i18n: string; chatOnly?: boolean }[] = [
	{ key: 'identity', i18n: 'identity' },
	{ key: 'context_config', i18n: 'context-config', chatOnly: true },
	{ key: 'react_config', i18n: 'react-config', chatOnly: true },
];

const toKebab = (s: string) => s.replace(/_/g, '-');

export function AgentFormFields({ schema, values, onChange, lockType }: Props) {
	const { t } = useTranslation();
	const agentType = (values.identity.agent_type as AgentType | undefined) ?? 'chat';

	const visibleSections = useMemo(
		() => SECTIONS.filter((s) => !s.chatOnly || agentType === 'chat'),
		[agentType],
	);

	return (
		<FieldGroup>
			<FieldSet>
				<FieldLegend>{t('agent-form.type.legend')}</FieldLegend>
				<FieldDescription>{t('agent-form.type.description')}</FieldDescription>
				<div className="flex gap-2">
					<Button
						type="button"
						variant={agentType === 'chat' ? 'default' : 'outline'}
						size="sm"
						className={cn('gap-1.5 flex-1', lockType && 'pointer-events-none')}
						disabled={lockType && agentType !== 'chat'}
						onClick={() => !lockType && onChange('identity', 'agent_type', 'chat')}
					>
						<MessageSquareText className="size-4" />
						{t('agent-form.type.chat')}
					</Button>
					<Button
						type="button"
						variant={agentType === 'realtime' ? 'default' : 'outline'}
						size="sm"
						className={cn('gap-1.5 flex-1', lockType && 'pointer-events-none')}
						disabled={lockType && agentType !== 'realtime'}
						onClick={() => !lockType && onChange('identity', 'agent_type', 'realtime')}
					>
						<AudioLines className="size-4" />
						{t('agent-form.type.realtime')}
					</Button>
				</div>
			</FieldSet>

			{visibleSections.map(({ key: sectionKey, i18n: sectionI18n }) => {
				const sectionSchema = schema[sectionKey];
				const legend = t(`agent-form.${sectionI18n}.legend`, {
					defaultValue: sectionSchema.title ?? sectionKey,
				});
				const description = t(`agent-form.${sectionI18n}.description`, {
					defaultValue: '',
				});
				return (
					<div key={sectionKey}>
						<FieldSeparator className="my-0" />
						<FieldSet>
							<FieldLegend>{legend}</FieldLegend>
							{description && <FieldDescription>{description}</FieldDescription>}
							<SchemaForm
								schema={sectionSchema}
								values={values[sectionKey]}
								onChange={(k, v) => onChange(sectionKey, k, v)}
								idPrefix={`agent-form-${sectionI18n}`}
								skipFields={
									sectionKey === 'identity' ? IDENTITY_SKIP_FIELDS : undefined
								}
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
