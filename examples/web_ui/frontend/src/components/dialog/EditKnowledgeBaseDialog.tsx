import { CheckCircle, CircleAlert, Loader2 } from 'lucide-react';
import { useEffect, useState } from 'react';

import type { KnowledgeBaseView } from '@/api';
import { Badge } from '@/components/ui/badge.tsx';
import { Button } from '@/components/ui/button.tsx';
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from '@/components/ui/dialog.tsx';
import { Field, FieldGroup, FieldLabel } from '@/components/ui/field.tsx';
import { Input } from '@/components/ui/input.tsx';
import { Textarea } from '@/components/ui/textarea.tsx';
import { useKnowledgeBases } from '@/hooks/useKnowledgeBases';
import { useTranslation } from '@/i18n/useI18n.ts';

interface Props {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	knowledgeBase: KnowledgeBaseView | null;
	onUpdated?: (view: KnowledgeBaseView) => void;
}

/**
 * Dialog to edit a knowledge base's mutable fields. Embedding model
 * is shown as a read-only badge because it's pinned at creation time
 * (the underlying collection is sized to its dimension).
 */
export function EditKnowledgeBaseDialog({ open, onOpenChange, knowledgeBase, onUpdated }: Props) {
	const { t } = useTranslation();
	const { update } = useKnowledgeBases();
	const [name, setName] = useState('');
	const [description, setDescription] = useState('');
	const [submitting, setSubmitting] = useState(false);
	const [errorKey, setErrorKey] = useState<string | null>(null);

	useEffect(() => {
		if (open && knowledgeBase) {
			setName(knowledgeBase.name);
			setDescription(knowledgeBase.description ?? '');
			setErrorKey(null);
			setSubmitting(false);
		}
	}, [open, knowledgeBase]);

	const handleSubmit = async () => {
		if (!knowledgeBase) return;
		const trimmedName = name.trim();
		if (!trimmedName) {
			setErrorKey('dialog-knowledge-base-edit.errors.nameRequired');
			return;
		}
		setErrorKey(null);
		setSubmitting(true);
		try {
			const view = await update(knowledgeBase.id, {
				name: trimmedName,
				description: description.trim(),
			});
			onUpdated?.(view);
			onOpenChange(false);
		} finally {
			setSubmitting(false);
		}
	};

	const embeddingModelLabel = knowledgeBase
		? `${knowledgeBase.embedding_model_config.model} · ${knowledgeBase.embedding_model_config.dimensions}d`
		: '';

	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogContent className="!w-[500px] !max-w-[500px]">
				<DialogHeader>
					<DialogTitle>{t('dialog-knowledge-base-edit.title')}</DialogTitle>
					<DialogDescription>
						{t('dialog-knowledge-base-edit.description')}
					</DialogDescription>
				</DialogHeader>
				<FieldGroup>
					<Field>
						<FieldLabel>{t('dialog-knowledge-base-edit.name.label')}</FieldLabel>
						<Input
							value={name}
							onChange={(e) => setName(e.target.value)}
							placeholder={t('dialog-knowledge-base-edit.name.placeholder')}
							disabled={submitting}
						/>
					</Field>
					<Field>
						<FieldLabel>
							{t('dialog-knowledge-base-edit.descriptionField.label')}
						</FieldLabel>
						<Textarea
							value={description}
							onChange={(e) => setDescription(e.target.value)}
							placeholder={t(
								'dialog-knowledge-base-edit.descriptionField.placeholder',
							)}
							disabled={submitting}
							rows={3}
						/>
					</Field>
					<Field orientation="horizontal">
						<FieldLabel>
							{t('dialog-knowledge-base-edit.embeddingModel.label')}
						</FieldLabel>
						<Badge variant="secondary" className="font-mono">
							{embeddingModelLabel}
						</Badge>
					</Field>
					{errorKey && <p className="text-destructive text-sm">{t(errorKey)}</p>}
				</FieldGroup>
				<DialogFooter>
					<Button
						variant="ghost"
						onClick={() => onOpenChange(false)}
						disabled={submitting}
					>
						<CircleAlert className="size-3.5" />
						{t('common.cancel')}
					</Button>
					<Button onClick={handleSubmit} disabled={submitting}>
						{submitting ? (
							<Loader2 className="size-3.5 animate-spin" />
						) : (
							<CheckCircle className="size-3.5" />
						)}
						{submitting ? t('common.saving') : t('common.save')}
					</Button>
				</DialogFooter>
			</DialogContent>
		</Dialog>
	);
}
