import { CircleAlert, Loader2, Save } from 'lucide-react';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import type { AgentRecord, ContextConfig, ReActConfig } from '@/api';
import {
	AgentFormFields,
	defaultAgentFormValues,
	type AgentFormValues,
	type AgentSection,
} from '@/components/form/AgentFormFields';
import type { SchemaFormValue } from '@/components/form/SchemaForm';
import { Button } from '@/components/ui/button';
import {
	Dialog,
	DialogContent,
	DialogFooter,
	DialogHeader,
	DialogTitle,
	DialogDescription,
} from '@/components/ui/dialog';
import { useAgents } from '@/hooks/useAgents';
import { useAgentSchema } from '@/hooks/useAgentSchema';

interface Props {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	agent: AgentRecord;
	onUpdated?: () => void;
}

export function EditAgentDialog({ open, onOpenChange, agent, onUpdated }: Props) {
	const { update } = useAgents();
	const { t } = useTranslation();
	const { schema } = useAgentSchema();
	const [submitting, setSubmitting] = useState(false);
	const [values, setValues] = useState<AgentFormValues | null>(null);

	useEffect(() => {
		if (!open || !schema) {
			if (!open) setValues(null);
			return;
		}
		// Start from schema defaults, then overlay the existing agent's data so
		// any unset fields fall back to defaults rather than empty.
		const base = defaultAgentFormValues(schema);
		const d = agent.data;
		setValues({
			identity: {
				...base.identity,
				agent_type: d.agent_type ?? 'chat',
				name: d.name,
				system_prompt: d.system_prompt,
			},
			context_config: { ...base.context_config, ...(d.context_config ?? {}) },
			react_config: { ...base.react_config, ...(d.react_config ?? {}) },
		});
	}, [open, schema, agent]);

	const handleChange = (section: AgentSection, key: string, value: SchemaFormValue) => {
		setValues((prev) =>
			prev ? { ...prev, [section]: { ...prev[section], [key]: value } } : prev,
		);
	};

	const handleSubmit = async () => {
		if (!values) return;
		const name = (values.identity.name as string | undefined)?.trim();
		if (!name) return;
		setSubmitting(true);
		try {
			await update(agent.id, {
				name,
				system_prompt: values.identity.system_prompt as string | undefined,
				context_config: values.context_config as unknown as ContextConfig,
				react_config: values.react_config as unknown as ReActConfig,
			});
			onOpenChange(false);
			onUpdated?.();
		} finally {
			setSubmitting(false);
		}
	};

	const nameValid = !!(values?.identity.name as string | undefined)?.trim();

	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogContent className="!w-[500px] !max-w-[500px]">
				<DialogHeader>
					<DialogTitle>{t('dialog-agent-edit.title')}</DialogTitle>
					<DialogDescription className="sr-only">
						{t('dialog-agent-edit.description')}
					</DialogDescription>
				</DialogHeader>
				<div className="no-scrollbar -mx-4 max-h-[75vh] overflow-y-auto px-4">
					{schema && values ? (
						<AgentFormFields
							schema={schema}
							values={values}
							onChange={handleChange}
							lockType
						/>
					) : (
						<p className="text-muted-foreground text-sm">{t('common.loading')}</p>
					)}
				</div>
				<DialogFooter>
					<Button
						variant="ghost"
						onClick={() => onOpenChange(false)}
						disabled={submitting}
					>
						<CircleAlert className="size-3.5" />
						{t('common.cancel')}
					</Button>
					<Button
						onClick={handleSubmit}
						disabled={!nameValid || submitting || !schema || !values}
					>
						{submitting ? (
							<Loader2 className="size-3.5 animate-spin" />
						) : (
							<Save className="size-3.5" />
						)}
						{submitting ? t('common.saving') : t('common.save')}
					</Button>
				</DialogFooter>
			</DialogContent>
		</Dialog>
	);
}
