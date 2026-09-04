import { CheckCircle, CircleAlert, Loader2, RefreshCw } from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';

import type { ChunkerInfo, JSONSchema, KnowledgeBaseView } from '@/api';
import {
	type SchemaFormValue,
	SchemaForm,
	defaultValuesFromSchema,
} from '@/components/form/SchemaForm';
import { ChunkerSelect } from '@/components/select/ChunkerSelect';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert.tsx';
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
import { Separator } from '@/components/ui/separator.tsx';
import { Textarea } from '@/components/ui/textarea.tsx';
import { useChunkers } from '@/hooks/useChunkers';
import { useKnowledgeBases } from '@/hooks/useKnowledgeBases';
import { useTranslation } from '@/i18n/useI18n.ts';
import { formatApiErrorForAlert } from '@/lib/api-error.ts';

interface Props {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	knowledgeBase: KnowledgeBaseView | null;
	onUpdated?: (view: KnowledgeBaseView) => void;
}

const NO_SKIP = new Set<string>();
const HORIZONTAL_FIELD_WIDTH = '[&>[data-orientation=horizontal]>:last-child]:w-48';

function formValues(parameters: Record<string, unknown>): Record<string, SchemaFormValue> {
	return parameters as Record<string, SchemaFormValue>;
}

function canonicalize(value: unknown): unknown {
	if (Array.isArray(value)) return value.map(canonicalize);
	if (value !== null && typeof value === 'object') {
		return Object.fromEntries(
			Object.entries(value as Record<string, unknown>)
				.sort(([left], [right]) => left.localeCompare(right))
				.map(([key, nestedValue]) => [key, canonicalize(nestedValue)]),
		);
	}
	return value;
}

function stableParameters(parameters: Record<string, unknown>): string {
	return JSON.stringify(canonicalize(parameters));
}

/**
 * Dialog to edit knowledge-base metadata and chunking. The embedding
 * model remains read-only because its dimension sizes the collection.
 */
export function EditKnowledgeBaseDialog({ open, onOpenChange, knowledgeBase, onUpdated }: Props) {
	const { t } = useTranslation();
	const { update } = useKnowledgeBases();
	const { chunkers, loading: chunkersLoading } = useChunkers();
	const [name, setName] = useState('');
	const [description, setDescription] = useState('');
	const [selectedChunkerType, setSelectedChunkerType] = useState('');
	const [chunkerParams, setChunkerParams] = useState<Record<string, SchemaFormValue>>({});
	const [submitting, setSubmitting] = useState(false);
	const [errorKey, setErrorKey] = useState<string | null>(null);
	const [errorMessage, setErrorMessage] = useState<string | null>(null);

	const selectedChunker = useMemo<ChunkerInfo | null>(
		() => chunkers.find((chunker) => chunker.type === selectedChunkerType) ?? null,
		[chunkers, selectedChunkerType],
	);

	const chunkerParamSchema = useMemo<JSONSchema | null>(
		() => selectedChunker?.parameter_schema ?? null,
		[selectedChunker],
	);

	const handleSelectChunker = useCallback(
		(type: string) => {
			setSelectedChunkerType(type);
			const schema = chunkers.find((chunker) => chunker.type === type)?.parameter_schema;
			setChunkerParams(schema ? defaultValuesFromSchema(schema, NO_SKIP) : {});
		},
		[chunkers],
	);

	const handleChunkerParamChange = useCallback((key: string, value: SchemaFormValue) => {
		setChunkerParams((previous) => ({ ...previous, [key]: value }));
	}, []);

	useEffect(() => {
		if (!open || !knowledgeBase) return;
		setName(knowledgeBase.name);
		setDescription(knowledgeBase.description ?? '');
		setErrorKey(null);
		setErrorMessage(null);
		setSubmitting(false);
	}, [open, knowledgeBase]);

	useEffect(() => {
		if (!open || !knowledgeBase || chunkersLoading) return;
		const current = knowledgeBase.chunker_config;
		if (current) {
			const schema = chunkers.find(
				(chunker) => chunker.type === current.type,
			)?.parameter_schema;
			setSelectedChunkerType(current.type);
			setChunkerParams({
				...(schema ? defaultValuesFromSchema(schema, NO_SKIP) : {}),
				...formValues(current.parameters),
			});
		} else if (chunkers.length > 0) {
			handleSelectChunker(chunkers[0].type);
		} else {
			setSelectedChunkerType('');
			setChunkerParams({});
		}
	}, [open, knowledgeBase, chunkers, chunkersLoading, handleSelectChunker]);

	const submittedParameters = useMemo<Record<string, unknown>>(() => {
		const parameters: Record<string, unknown> = {};
		for (const [key, value] of Object.entries(chunkerParams)) {
			if (value !== undefined && value !== null && value !== '') parameters[key] = value;
		}
		return parameters;
	}, [chunkerParams]);

	const chunkerChanged = useMemo(() => {
		if (!knowledgeBase || !selectedChunkerType) return false;
		const current = knowledgeBase.chunker_config;
		const currentType = current?.type ?? 'approx_token';
		const currentSchema = chunkers.find(
			(chunker) => chunker.type === currentType,
		)?.parameter_schema;
		const currentParameters = {
			...(currentSchema ? defaultValuesFromSchema(currentSchema, NO_SKIP) : {}),
			...(current?.parameters ?? {}),
		};
		return (
			currentType !== selectedChunkerType ||
			stableParameters(currentParameters) !== stableParameters(submittedParameters)
		);
	}, [chunkers, knowledgeBase, selectedChunkerType, submittedParameters]);

	const willReindex = chunkerChanged && (knowledgeBase?.document_count ?? 0) > 0;

	const handleSubmit = async () => {
		if (!knowledgeBase) return;
		const trimmedName = name.trim();
		if (!trimmedName) {
			setErrorKey('dialog-knowledge-base-edit.errors.nameRequired');
			return;
		}
		if (chunkers.length > 0 && !selectedChunkerType) {
			setErrorKey('dialog-knowledge-base-edit.errors.chunkerRequired');
			return;
		}
		setErrorKey(null);
		setErrorMessage(null);
		setSubmitting(true);
		try {
			const view = await update(knowledgeBase.id, {
				name: trimmedName,
				description: description.trim(),
				...(selectedChunkerType
					? {
							chunker_config: {
								type: selectedChunkerType,
								parameters: submittedParameters,
							},
						}
					: {}),
			});
			onUpdated?.(view);
			onOpenChange(false);
		} catch (error) {
			setErrorMessage(formatApiErrorForAlert(error));
		} finally {
			setSubmitting(false);
		}
	};

	const embeddingModelLabel = knowledgeBase
		? `${knowledgeBase.embedding_model_config.model} · ${knowledgeBase.embedding_model_config.dimensions}d`
		: '';

	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogContent className="!w-[560px] !max-w-[calc(100vw-2rem)] max-h-[calc(100vh-2rem)] overflow-y-auto">
				<DialogHeader>
					<DialogTitle>{t('dialog-knowledge-base-edit.title')}</DialogTitle>
					<DialogDescription>
						{t('dialog-knowledge-base-edit.description')}
					</DialogDescription>
				</DialogHeader>
				{willReindex && knowledgeBase && (
					<Alert>
						<RefreshCw className="size-4" />
						<AlertTitle>{t('dialog-knowledge-base-edit.reindex.title')}</AlertTitle>
						<AlertDescription>
							{t('dialog-knowledge-base-edit.reindex.description', {
								documents: knowledgeBase.document_count,
							})}
						</AlertDescription>
					</Alert>
				)}
				<FieldGroup className={HORIZONTAL_FIELD_WIDTH}>
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
					<Separator />
					<Field orientation="horizontal">
						<FieldLabel>
							{t('dialog-knowledge-base-edit.embeddingModel.label')}
						</FieldLabel>
						<Badge variant="secondary" className="font-mono">
							{embeddingModelLabel}
						</Badge>
					</Field>
					{chunkers.length > 0 && (
						<>
							<Field orientation="horizontal">
								<FieldLabel>
									{t('dialog-knowledge-base-edit.chunker.label')}
								</FieldLabel>
								<ChunkerSelect
									value={selectedChunkerType}
									chunkers={chunkers}
									loading={chunkersLoading}
									onChange={handleSelectChunker}
									disabled={submitting}
								/>
							</Field>
							{chunkerParamSchema && (
								<SchemaForm
									schema={chunkerParamSchema}
									values={chunkerParams}
									onChange={handleChunkerParamChange}
									skipFields={NO_SKIP}
									idPrefix="edit-chunker-param"
									orientation="horizontal"
									className={HORIZONTAL_FIELD_WIDTH}
									labelFor={(key, property) =>
										t(
											`chunker-types.${selectedChunkerType}.params.${key}.label`,
											{ defaultValue: '' },
										) ||
										property.title ||
										undefined
									}
									descriptionFor={(key, property) =>
										t(
											`chunker-types.${selectedChunkerType}.params.${key}.description`,
											{ defaultValue: '' },
										) ||
										property.description ||
										undefined
									}
								/>
							)}
						</>
					)}
					{errorKey && <p className="text-destructive text-sm">{t(errorKey)}</p>}
					{errorMessage && <p className="text-destructive text-sm">{errorMessage}</p>}
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
						{submitting
							? t('common.saving')
							: willReindex
								? t('dialog-knowledge-base-edit.reindex.submit')
								: t('common.save')}
					</Button>
				</DialogFooter>
			</DialogContent>
		</Dialog>
	);
}
