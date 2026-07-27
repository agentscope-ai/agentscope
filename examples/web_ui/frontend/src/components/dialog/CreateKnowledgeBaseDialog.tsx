import { CircleAlert, Info, Loader2, PlusCircle } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';

import type { EmbeddingModelCard, EmbeddingModelConfig } from '@/api';
import { DimensionSelect } from '@/components/select/DimensionSelect';
import { EmbeddingSelect } from '@/components/select/EmbeddingSelect';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert.tsx';
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
import { useKbEmbeddingModels } from '@/hooks/useKbEmbeddingModels';
import { useKnowledgeBases } from '@/hooks/useKnowledgeBases';
import { useTranslation } from '@/i18n/useI18n.ts';

interface Props {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	onCreated?: (knowledgeBaseId: string) => void;
	onAddCredential?: () => void;
	/**
	 * Bumped externally (e.g. after a credential is created) to ask the
	 * embedding selector to refetch its options.
	 */
	credentialRefetchTrigger?: number;
}

interface SelectedEmbedding {
	type: string;
	credentialId: string;
	model: string;
	card: EmbeddingModelCard;
}

export function CreateKnowledgeBaseDialog({
	open,
	onOpenChange,
	onCreated,
	onAddCredential,
	credentialRefetchTrigger,
}: Props) {
	const { t } = useTranslation();
	const { create } = useKnowledgeBases();
	const { providers, policy, loading } = useKbEmbeddingModels(credentialRefetchTrigger);

	const [name, setName] = useState('');
	const [description, setDescription] = useState('');
	const [selected, setSelected] = useState<SelectedEmbedding | null>(null);
	const [dimension, setDimension] = useState<number | null>(null);
	const [submitting, setSubmitting] = useState(false);
	const [errorKey, setErrorKey] = useState<string | null>(null);

	useEffect(() => {
		if (!open) {
			setName('');
			setDescription('');
			setSelected(null);
			setDimension(null);
			setErrorKey(null);
			setSubmitting(false);
		}
	}, [open]);

	const dimensionOptions = useMemo<number[] | null>(() => {
		if (!selected) return null;
		const sd = selected.card.supported_dimensions;
		return sd && sd.length > 0 ? sd : [selected.card.dimensions];
	}, [selected]);

	const handleSelectEmbedding = (sel: SelectedEmbedding) => {
		setSelected(sel);
		const sd = sel.card.supported_dimensions;
		const defaultDim =
			sd && sd.length > 0
				? sd.includes(sel.card.dimensions)
					? sel.card.dimensions
					: sd[0]
				: sel.card.dimensions;
		setDimension(defaultDim);
	};

	const handleSubmit = async () => {
		if (!name.trim()) {
			setErrorKey('dialog-knowledge-base-create.errors.nameRequired');
			return;
		}
		if (!selected) {
			setErrorKey('dialog-knowledge-base-create.errors.embeddingRequired');
			return;
		}
		if (dimension == null || dimension <= 0) {
			setErrorKey('dialog-knowledge-base-create.errors.dimensionRequired');
			return;
		}
		setErrorKey(null);
		setSubmitting(true);
		try {
			const config: EmbeddingModelConfig = {
				type: selected.type,
				credential_id: selected.credentialId,
				model: selected.model,
				dimensions: dimension,
				parameters: {},
			};
			const knowledgeBaseId = await create({
				name: name.trim(),
				description: description.trim(),
				embedding_model_config: config,
			});
			onCreated?.(knowledgeBaseId);
			onOpenChange(false);
		} finally {
			setSubmitting(false);
		}
	};

	const isLockedPolicy = policy && policy.kind !== 'any';
	const noCompatibleModels = !loading && providers.length === 0;

	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogContent className="!w-[500px] !max-w-[500px]">
				<DialogHeader>
					<DialogTitle>{t('dialog-knowledge-base-create.title')}</DialogTitle>
					<DialogDescription>
						{t('dialog-knowledge-base-create.description')}
					</DialogDescription>
				</DialogHeader>

				{isLockedPolicy && policy?.dimension != null && (
					<Alert>
						<Info className="size-4" />
						<AlertTitle>
							{t('dialog-knowledge-base-create.policy.lockedTitle', {
								dimension: policy.dimension,
							})}
						</AlertTitle>
						<AlertDescription>
							{t('dialog-knowledge-base-create.policy.lockedDescription', {
								dimension: policy.dimension,
							})}
						</AlertDescription>
					</Alert>
				)}

				{noCompatibleModels && (
					<Alert variant="destructive">
						<CircleAlert className="size-4" />
						<AlertTitle>
							{t('dialog-knowledge-base-create.policy.noCompatibleTitle')}
						</AlertTitle>
						<AlertDescription>
							{isLockedPolicy && policy?.dimension != null
								? t('dialog-knowledge-base-create.policy.noCompatibleLocked', {
										dimension: policy.dimension,
									})
								: t('dialog-knowledge-base-create.policy.noCompatibleAny')}
						</AlertDescription>
					</Alert>
				)}

				<FieldGroup>
					<Field>
						<FieldLabel>{t('dialog-knowledge-base-create.name.label')}</FieldLabel>
						<Input
							value={name}
							onChange={(e) => setName(e.target.value)}
							placeholder={t('dialog-knowledge-base-create.name.placeholder')}
							disabled={submitting}
						/>
					</Field>
					<Field>
						<FieldLabel>
							{t('dialog-knowledge-base-create.descriptionField.label')}
						</FieldLabel>
						<Textarea
							value={description}
							onChange={(e) => setDescription(e.target.value)}
							placeholder={t(
								'dialog-knowledge-base-create.descriptionField.placeholder',
							)}
							disabled={submitting}
							rows={3}
						/>
					</Field>
					<Field orientation="horizontal">
						<FieldLabel>
							{t('dialog-knowledge-base-create.embeddingModel.label')}
						</FieldLabel>
						<EmbeddingSelect
							value={
								selected
									? {
											type: selected.type,
											credential_id: selected.credentialId,
											model: selected.model,
										}
									: null
							}
							providers={providers}
							loading={loading}
							onChange={handleSelectEmbedding}
							onAddCredential={onAddCredential}
						/>
					</Field>
					<Field orientation="horizontal">
						<FieldLabel>{t('dialog-knowledge-base-create.dimension.label')}</FieldLabel>
						<DimensionSelect
							value={dimension}
							options={dimensionOptions}
							onChange={setDimension}
							disabled={submitting}
						/>
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
					<Button onClick={handleSubmit} disabled={submitting || noCompatibleModels}>
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
