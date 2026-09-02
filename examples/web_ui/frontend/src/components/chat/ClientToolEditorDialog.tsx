import { Braces, Save } from 'lucide-react';
import { type FormEvent, useId, useState } from 'react';

import { Button } from '@/components/ui/button';
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { useTranslation } from '@/i18n/useI18n';
import {
	saveCustomClientExternalTool,
	type CustomClientExternalTool,
	type CustomClientExternalToolDraft,
	type CustomToolValidationError,
} from '@/lib/client-external-tool-store';

const EMPTY_SCHEMA = JSON.stringify(
	{
		type: 'object',
		properties: {},
		additionalProperties: false,
	},
	null,
	2,
);

function getInitialDraft(tool: CustomClientExternalTool | null): CustomClientExternalToolDraft {
	if (!tool) {
		return { displayName: '', name: '', description: '', inputSchema: EMPTY_SCHEMA };
	}
	return {
		displayName: tool.display_name,
		name: tool.definition.name.replace(/^client__/, ''),
		description: tool.definition.description,
		inputSchema: JSON.stringify(tool.definition.input_schema, null, 2),
	};
}

export function ClientToolEditorDialog({
	tool,
	agentId,
	sessionId,
	onClose,
}: {
	tool: CustomClientExternalTool | null;
	agentId: string | null;
	sessionId: string | null;
	onClose: () => void;
}) {
	const { t } = useTranslation();
	const baseId = useId();
	const [draft, setDraft] = useState(() => getInitialDraft(tool));
	const [error, setError] = useState<CustomToolValidationError | null>(null);

	const setField = (field: keyof CustomClientExternalToolDraft, value: string) => {
		setDraft((current) => ({ ...current, [field]: value }));
		if (error?.field === field) setError(null);
	};

	const handleSubmit = (event: FormEvent) => {
		event.preventDefault();
		const result = saveCustomClientExternalTool(
			draft,
			tool?.definition.name ?? null,
			agentId,
			sessionId,
		);
		if (!result.ok) {
			setError(result.error);
			return;
		}
		onClose();
	};

	const fieldError = (field: CustomToolValidationError['field']) => {
		if (error?.field !== field) return null;
		return (
			<p className="text-xs leading-5 text-destructive" role="alert">
				{t(`clientTools.validation.${error.code}`)}
			</p>
		);
	};

	return (
		<Dialog open onOpenChange={(open) => !open && onClose()}>
			<DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-xl">
				<DialogHeader>
					<DialogTitle>
						{t(tool ? 'clientTools.editor.editTitle' : 'clientTools.editor.addTitle')}
					</DialogTitle>
					<DialogDescription>{t('clientTools.editor.description')}</DialogDescription>
				</DialogHeader>

				<form id={`${baseId}-form`} className="space-y-4" onSubmit={handleSubmit}>
					<div className="space-y-1.5">
						<Label htmlFor={`${baseId}-display-name`}>
							{t('clientTools.editor.displayName')}
						</Label>
						<Input
							id={`${baseId}-display-name`}
							value={draft.displayName}
							maxLength={80}
							autoFocus
							aria-invalid={error?.field === 'displayName'}
							placeholder={t('clientTools.editor.displayNamePlaceholder')}
							onChange={(event) => setField('displayName', event.target.value)}
						/>
						{fieldError('displayName')}
					</div>

					<div className="space-y-1.5">
						<Label htmlFor={`${baseId}-name`}>{t('clientTools.editor.toolName')}</Label>
						<div className="flex rounded-lg border border-input bg-background focus-within:border-ring focus-within:ring-3 focus-within:ring-ring/50">
							<span className="flex items-center border-r border-input px-2.5 font-mono text-xs text-muted-foreground">
								client__
							</span>
							<Input
								id={`${baseId}-name`}
								value={draft.name}
								maxLength={56}
								aria-invalid={error?.field === 'name'}
								className="border-0 font-mono focus-visible:ring-0"
								placeholder="collect_feedback"
								onChange={(event) => setField('name', event.target.value)}
							/>
						</div>
						<p className="text-xs leading-5 text-muted-foreground">
							{t('clientTools.editor.toolNameHint')}
						</p>
						{fieldError('name')}
					</div>

					<div className="space-y-1.5">
						<Label htmlFor={`${baseId}-description`}>
							{t('clientTools.editor.modelDescription')}
						</Label>
						<Textarea
							id={`${baseId}-description`}
							value={draft.description}
							maxLength={2000}
							aria-invalid={error?.field === 'description'}
							className="min-h-20 resize-y"
							placeholder={t('clientTools.editor.descriptionPlaceholder')}
							onChange={(event) => setField('description', event.target.value)}
						/>
						{fieldError('description')}
					</div>

					<div className="space-y-1.5">
						<Label htmlFor={`${baseId}-schema`} className="gap-1.5">
							<Braces className="size-3.5 text-muted-foreground" />
							{t('clientTools.inputSchema')}
						</Label>
						<Textarea
							id={`${baseId}-schema`}
							value={draft.inputSchema}
							spellCheck={false}
							aria-invalid={error?.field === 'inputSchema'}
							className="min-h-52 resize-y font-mono text-xs leading-5"
							onChange={(event) => setField('inputSchema', event.target.value)}
						/>
						<p className="text-xs leading-5 text-muted-foreground">
							{t('clientTools.editor.schemaHint')}
						</p>
						<p className="text-xs leading-5 text-muted-foreground">
							{t('clientTools.editor.permissionHint')}
						</p>
						{fieldError('inputSchema')}
					</div>
				</form>

				<DialogFooter>
					<Button type="button" variant="ghost" onClick={onClose}>
						{t('common.cancel')}
					</Button>
					<Button type="submit" form={`${baseId}-form`}>
						<Save className="size-3.5" />
						{t('common.save')}
					</Button>
				</DialogFooter>
			</DialogContent>
		</Dialog>
	);
}
