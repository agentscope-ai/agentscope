import { PlusCircle } from 'lucide-react';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import type { ContextConfig, ReActConfig } from '@/api';
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
	DialogTrigger,
} from '@/components/ui/dialog';
import { useAgents } from '@/hooks/useAgents';
import { useAgentSchema } from '@/hooks/useAgentSchema';

interface Props {
	onCreated?: () => void;
	triggerId?: string;
}

export function AgentDialog({ onCreated, triggerId }: Props) {
	const { create } = useAgents();
	const { t } = useTranslation();
	const { schema } = useAgentSchema();
	const [open, setOpen] = useState(false);
	const [submitting, setSubmitting] = useState(false);
	const [values, setValues] = useState<AgentFormValues | null>(null);

	useEffect(() => {
		if (open && schema && !values) {
			setValues(defaultAgentFormValues(schema));
		}
		if (!open) setValues(null);
	}, [open, schema, values]);

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
			await create({
				name,
				system_prompt: values.identity.system_prompt as string | undefined,
				context_config: values.context_config as unknown as ContextConfig,
				react_config: values.react_config as unknown as ReActConfig,
			});
			setOpen(false);
			onCreated?.();
		} finally {
			setSubmitting(false);
		}
	};

	const nameValid = !!(values?.identity.name as string | undefined)?.trim();

	return (
		<Dialog open={open} onOpenChange={setOpen}>
			<DialogTrigger asChild>
				<Button id={triggerId} size="sm">
					<PlusCircle />
					<span>{t('dialog-agent-create.trigger')}</span>
				</Button>
			</DialogTrigger>
			<DialogContent className="w-[500px]! max-w-[500px]!">
				<DialogHeader>
					<DialogTitle>{t('dialog-agent-create.title')}</DialogTitle>
				</DialogHeader>
				<div className="no-scrollbar -mx-4 max-h-[75vh] overflow-y-auto px-4">
					{schema && values ? (
						<AgentFormFields schema={schema} values={values} onChange={handleChange} />
					) : (
						<p className="text-muted-foreground text-sm">{t('common.loading')}</p>
					)}
				</div>
				<DialogFooter>
					<Button size="sm" variant="outline" onClick={() => setOpen(false)}>
						{t('common.cancel')}
					</Button>
					<Button
						size="sm"
						onClick={handleSubmit}
						disabled={!nameValid || submitting || !schema || !values}
					>
						<PlusCircle />
						{submitting ? t('common.creating') : t('common.create')}
					</Button>
				</DialogFooter>
			</DialogContent>
		</Dialog>
	);
}
