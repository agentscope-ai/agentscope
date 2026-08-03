import { CircleAlert, Loader2, PlusCircle } from 'lucide-react';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import type { ContextConfig, InviteConfig, ReActConfig } from '@/api';
import type { AgentBindings } from '@/components/form/AgentBindingFields';
import {
	defaultAgentFormValues,
	type AgentFormValues,
	type AgentSection,
} from '@/components/form/AgentFormFields';
import { AgentFormTabs } from '@/components/form/AgentFormTabs';
import type { SchemaFormValue } from '@/components/form/SchemaForm';
import { Alert, AlertDescription } from '@/components/ui/alert.tsx';
import { Button } from '@/components/ui/button';
import {
	Dialog,
	DialogContent,
	DialogFooter,
	DialogHeader,
	DialogTitle,
	DialogDescription,
	DialogTrigger,
} from '@/components/ui/dialog';
import { useAgents } from '@/hooks/useAgents';
import { useAgentSchema } from '@/hooks/useAgentSchema';
import { formatApiErrorForAlert } from '@/lib/api-error';

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
	const [bindings, setBindings] = useState<AgentBindings>({
		mcp_ids: [],
		skill_ids: [],
	});
	const [errorMsg, setErrorMsg] = useState('');

	useEffect(() => {
		if (open && schema && !values) {
			setValues(defaultAgentFormValues(schema));
		}
		if (!open) {
			setValues(null);
			setBindings({ mcp_ids: [], skill_ids: [] });
			setErrorMsg('');
		}
	}, [open, schema, values]);

	const handleChange = (section: AgentSection, key: string, value: SchemaFormValue) => {
		setErrorMsg('');
		setValues((prev) =>
			prev ? { ...prev, [section]: { ...prev[section], [key]: value } } : prev,
		);
	};

	const handleSubmit = async () => {
		if (!values) return;
		const name = (values.identity.name as string | undefined)?.trim();
		if (!name) return;
		setErrorMsg('');
		setSubmitting(true);
		try {
			await create(
				{
					name,
					system_prompt: values.identity.system_prompt as string | undefined,
					context_config: values.context_config as unknown as ContextConfig,
					react_config: values.react_config as unknown as ReActConfig,
					invite_config: values.invite_config as unknown as InviteConfig,
					...bindings,
				},
				{ silent: true },
			);
			setOpen(false);
			onCreated?.();
		} catch (e) {
			setErrorMsg(formatApiErrorForAlert(e));
		} finally {
			setSubmitting(false);
		}
	};

	const nameValid = !!(values?.identity.name as string | undefined)?.trim();

	return (
		<Dialog open={open} onOpenChange={setOpen}>
			<DialogTrigger asChild>
				<Button id={triggerId}>
					<PlusCircle />
					<span>{t('dialog-agent-create.trigger')}</span>
				</Button>
			</DialogTrigger>
			<DialogContent className="sm:max-w-2xl">
				<DialogHeader>
					<DialogTitle>{t('dialog-agent-create.title')}</DialogTitle>
					<DialogDescription className="sr-only">
						{t('dialog-agent-create.description')}
					</DialogDescription>
				</DialogHeader>
				{schema && values ? (
					<AgentFormTabs
						schema={schema}
						values={values}
						onChange={handleChange}
						bindings={bindings}
						onBindingsChange={setBindings}
					/>
				) : (
					<p className="text-muted-foreground text-sm">{t('common.loading')}</p>
				)}
				{errorMsg && (
					<Alert variant="destructive">
						<CircleAlert />
						<AlertDescription className="whitespace-pre-wrap">
							{errorMsg}
						</AlertDescription>
					</Alert>
				)}
				<DialogFooter>
					<Button variant="ghost" onClick={() => setOpen(false)} disabled={submitting}>
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
							<PlusCircle className="size-3.5" />
						)}
						{submitting ? t('common.creating') : t('common.create')}
					</Button>
				</DialogFooter>
			</DialogContent>
		</Dialog>
	);
}
