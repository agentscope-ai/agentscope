import { useState, useEffect } from 'react';

import { credentialApi } from '@/api';
import type { CredentialRecord, CredentialSchema } from '@/api';
import { SchemaForm } from '@/components/form/SchemaForm';
import { Button } from '@/components/ui/button';
import {
	Dialog,
	DialogContent,
	DialogHeader,
	DialogTitle,
	DialogFooter,
} from '@/components/ui/dialog';
import { useCredentials } from '@/hooks/useCredentials';
import { useTranslation } from '@/i18n/useI18n';

interface Props {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	credential: CredentialRecord;
	onUpdated?: () => void;
}

export function EditCredentialDialog({ open, onOpenChange, credential, onUpdated }: Props) {
	const { update } = useCredentials();
	const { t } = useTranslation();
	const [schema, setSchema] = useState<CredentialSchema | null>(null);
	const [loadingSchema, setLoadingSchema] = useState(false);
	const [values, setValues] = useState<Record<string, string | boolean>>({});
	const [submitting, setSubmitting] = useState(false);

	const type = credential.data.type as string | undefined;

	useEffect(() => {
		if (!open || !type) return;
		setLoadingSchema(true);
		credentialApi
			.schemas()
			.then((res) => {
				const matched = res.schemas.find(
					(s) => (s.properties.type?.const as string) === type,
				);
				setSchema(matched ?? null);
				// Pre-fill values from existing credential data (excluding writeOnly fields)
				if (matched) {
					const prefill: Record<string, string | boolean> = {};
					for (const [key, prop] of Object.entries(matched.properties)) {
						if (key === 'id' || key === 'type' || prop.const !== undefined) continue;
						if (prop.writeOnly) continue; // don't pre-fill secrets like api_key
						const existing = credential.data[key];
						if (existing !== undefined) {
							prefill[key] = existing as string | boolean;
						}
					}
					setValues(prefill);
				}
			})
			.finally(() => setLoadingSchema(false));
	}, [open, type, credential.data]);

	const handleSubmit = async () => {
		if (!schema) return;
		setSubmitting(true);
		try {
			const data: Record<string, unknown> = { ...credential.data };
			for (const [key, prop] of Object.entries(schema.properties)) {
				if (key === 'id' || key === 'type' || prop.const !== undefined) continue;
				const val = values[key];
				if (val !== undefined && val !== '') data[key] = val;
			}
			await update(credential.id, { data });
			onOpenChange(false);
			onUpdated?.();
		} finally {
			setSubmitting(false);
		}
	};

	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogContent>
				<DialogHeader>
					<DialogTitle>{t('dialog-credential-edit.title')}</DialogTitle>
				</DialogHeader>
				{loadingSchema ? (
					<p className="text-muted-foreground text-sm">{t('common.loading')}</p>
				) : schema ? (
					<SchemaForm
						schema={schema}
						values={values}
						onChange={(key, val) => setValues((prev) => ({ ...prev, [key]: val }))}
					/>
				) : null}
				<DialogFooter>
					<Button variant="outline" onClick={() => onOpenChange(false)}>
						{t('common.cancel')}
					</Button>
					<Button onClick={handleSubmit} disabled={submitting || !schema}>
						{submitting ? t('common.saving') : t('common.save')}
					</Button>
				</DialogFooter>
			</DialogContent>
		</Dialog>
	);
}
