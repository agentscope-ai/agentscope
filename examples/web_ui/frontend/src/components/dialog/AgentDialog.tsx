import { PlusCircle } from 'lucide-react';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import {
	Dialog,
	DialogContent,
	DialogHeader,
	DialogTitle,
	DialogTrigger,
} from '@/components/ui/dialog';
import {
	Field,
	FieldDescription,
	FieldGroup,
	FieldLabel,
	FieldLegend,
	FieldSeparator,
	FieldSet,
} from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { useAgents } from '@/hooks/useAgents';

interface Props {
	onCreated?: () => void;
	triggerId?: string;
}

export function AgentDialog({ onCreated, triggerId }: Props) {
	const { create } = useAgents();
	const { t } = useTranslation();
	const [open, setOpen] = useState(false);
	const [submitting, setSubmitting] = useState(false);

	const [name, setName] = useState('');
	const [systemPrompt, setSystemPrompt] = useState("You're a helpful assistant.");
	const [triggerRatio, setTriggerRatio] = useState('');
	const [reserveRatio, setReserveRatio] = useState('');
	const [toolResultLimit, setToolResultLimit] = useState('');
	const [compressionPrompt, setCompressionPrompt] = useState('');
	const [summaryTemplate, setSummaryTemplate] = useState('');
	const [maxIters, setMaxIters] = useState('');
	const [stopOnReject, setStopOnReject] = useState(false);

	const handleSubmit = async () => {
		const context_config = {
			...(triggerRatio !== '' && { trigger_ratio: parseFloat(triggerRatio) }),
			...(reserveRatio !== '' && { reserve_ratio: parseFloat(reserveRatio) }),
			...(toolResultLimit !== '' && { tool_result_limit: parseInt(toolResultLimit) }),
			...(compressionPrompt !== '' && { compression_prompt: compressionPrompt }),
			...(summaryTemplate !== '' && { summary_template: summaryTemplate }),
		};
		const react_config = {
			...(maxIters !== '' && { max_iters: parseInt(maxIters) }),
			stop_on_reject: stopOnReject,
		};
		setSubmitting(true);
		try {
			await create({ name, system_prompt: systemPrompt, context_config, react_config });
			setOpen(false);
			onCreated?.();
		} finally {
			setSubmitting(false);
		}
	};

	return (
		<Dialog open={open} onOpenChange={setOpen}>
			<DialogTrigger asChild>
				<Button id={triggerId} size="sm">
					<PlusCircle />
					<span>{t('dialog-agent-create.trigger')}</span>
				</Button>
			</DialogTrigger>
			<DialogContent className="max-w-lg max-h-[80vh] overflow-y-auto">
				<DialogHeader>
					<DialogTitle>{t('dialog-agent-create.title')}</DialogTitle>
				</DialogHeader>
				<FieldGroup>
					<FieldSet>
						<FieldLegend>{t('dialog-agent-create.identity.legend')}</FieldLegend>
						<FieldDescription>
							{t('dialog-agent-create.identity.description')}
						</FieldDescription>
						<FieldGroup>
							<Field>
								<FieldLabel htmlFor="agent-name">
									{t('dialog-agent-create.identity.name.label')}
								</FieldLabel>
								<Input
									id="agent-name"
									placeholder={t('dialog-agent-create.identity.name.placeholder')}
									value={name}
									onChange={(e) => setName(e.target.value)}
								/>
							</Field>
							<Field>
								<FieldLabel htmlFor="agent-prompt">
									{t('dialog-agent-create.identity.systemPrompt.label')}
								</FieldLabel>
								<Textarea
									id="agent-prompt"
									rows={4}
									placeholder={t(
										'dialog-agent-create.identity.systemPrompt.placeholder',
									)}
									value={systemPrompt}
									onChange={(e) => setSystemPrompt(e.target.value)}
								/>
							</Field>
						</FieldGroup>
					</FieldSet>
					<FieldSeparator />
					<FieldSet>
						<FieldLegend>{t('dialog-agent-create.contextConfig.legend')}</FieldLegend>
						<FieldDescription>
							{t('dialog-agent-create.contextConfig.description')}
						</FieldDescription>
						<FieldGroup>
							<Field>
								<FieldLabel htmlFor="trigger-ratio">
									{t('dialog-agent-create.contextConfig.triggerRatio.label')}
								</FieldLabel>
								<Input
									id="trigger-ratio"
									type="number"
									min={0.01}
									max={0.89}
									step={0.01}
									placeholder={t(
										'dialog-agent-create.contextConfig.triggerRatio.placeholder',
									)}
									value={triggerRatio}
									onChange={(e) => setTriggerRatio(e.target.value)}
								/>
							</Field>
							<Field>
								<FieldLabel htmlFor="reserve-ratio">
									{t('dialog-agent-create.contextConfig.reserveRatio.label')}
								</FieldLabel>
								<Input
									id="reserve-ratio"
									type="number"
									min={0.01}
									max={0.89}
									step={0.01}
									placeholder={t(
										'dialog-agent-create.contextConfig.reserveRatio.placeholder',
									)}
									value={reserveRatio}
									onChange={(e) => setReserveRatio(e.target.value)}
								/>
							</Field>
							<Field>
								<FieldLabel htmlFor="tool-result-limit">
									{t('dialog-agent-create.contextConfig.toolResultLimit.label')}
								</FieldLabel>
								<Input
									id="tool-result-limit"
									type="number"
									min={100}
									step={100}
									placeholder={t(
										'dialog-agent-create.contextConfig.toolResultLimit.placeholder',
									)}
									value={toolResultLimit}
									onChange={(e) => setToolResultLimit(e.target.value)}
								/>
							</Field>
							<Field>
								<FieldLabel htmlFor="compression-prompt">
									{t('dialog-agent-create.contextConfig.compressionPrompt.label')}
								</FieldLabel>
								<Textarea
									id="compression-prompt"
									rows={2}
									placeholder={t(
										'dialog-agent-create.contextConfig.compressionPrompt.placeholder',
									)}
									value={compressionPrompt}
									onChange={(e) => setCompressionPrompt(e.target.value)}
								/>
							</Field>
							<Field>
								<FieldLabel htmlFor="summary-template">
									{t('dialog-agent-create.contextConfig.summaryTemplate.label')}
								</FieldLabel>
								<Textarea
									id="summary-template"
									rows={2}
									placeholder={t(
										'dialog-agent-create.contextConfig.summaryTemplate.placeholder',
									)}
									value={summaryTemplate}
									onChange={(e) => setSummaryTemplate(e.target.value)}
								/>
							</Field>
						</FieldGroup>
					</FieldSet>
					<FieldSeparator />
					<FieldSet>
						<FieldLegend>{t('dialog-agent-create.reactConfig.legend')}</FieldLegend>
						<FieldDescription>
							{t('dialog-agent-create.reactConfig.description')}
						</FieldDescription>
						<FieldGroup>
							<Field>
								<FieldLabel htmlFor="max-iters">
									{t('dialog-agent-create.reactConfig.maxIters.label')}
								</FieldLabel>
								<Input
									id="max-iters"
									type="number"
									min={1}
									step={1}
									placeholder={t(
										'dialog-agent-create.reactConfig.maxIters.placeholder',
									)}
									value={maxIters}
									onChange={(e) => setMaxIters(e.target.value)}
								/>
							</Field>
							<Field orientation="horizontal">
								<Checkbox
									id="stop-on-reject"
									checked={stopOnReject}
									onCheckedChange={(v) => setStopOnReject(v === true)}
								/>
								<FieldLabel htmlFor="stop-on-reject" className="font-normal">
									{t('dialog-agent-create.reactConfig.stopOnReject.label')}
								</FieldLabel>
							</Field>
						</FieldGroup>
					</FieldSet>
					<Field orientation="horizontal">
						<Button onClick={handleSubmit} disabled={!name.trim() || submitting}>
							<PlusCircle />
							{submitting ? t('common.creating') : t('common.create')}
						</Button>
						<Button variant="outline" onClick={() => setOpen(false)}>
							{t('common.cancel')}
						</Button>
					</Field>
				</FieldGroup>
			</DialogContent>
		</Dialog>
	);
}
