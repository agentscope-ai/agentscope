import { CircleAlert } from 'lucide-react';
import { useEffect, useState } from 'react';

import { hubApi, mcpApi } from '@/api';
import type { JSONSchema, MCPCard, MCPView } from '@/api';
import { ApiError } from '@/api/client';
import { SchemaForm, defaultValuesFromSchema } from '@/components/form/SchemaForm.tsx';
import type { SchemaFormValue } from '@/components/form/SchemaForm.tsx';
import { Alert, AlertDescription } from '@/components/ui/alert.tsx';
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
import { Spinner } from '@/components/ui/spinner.tsx';
import { useTranslation } from '@/i18n/useI18n';

interface Props {
	/** The card to install, or `null` to keep the dialog closed. */
	card: MCPCard | null;
	/**
	 * Set to edit an MCP already in the library instead of installing a new
	 * one: the same card, the same form, a PATCH instead of a POST.
	 */
	editing?: MCPView | null;
	onOpenChange: (open: boolean) => void;
	onInstalled: () => void;
}

/**
 * Collects a card's `inputs_schema` answers and installs — or re-keys — it.
 *
 * The values go straight to the server, which renders the config template.
 * Placeholders are never substituted here, since the rendered config is what
 * holds the secret; for the same reason the form starts blank on an edit
 * rather than pre-filling secrets the server never sends back.
 */
export function InstallMCPDialog({ card, editing, onOpenChange, onInstalled }: Props) {
	const { t } = useTranslation();
	const [name, setName] = useState('');
	const [values, setValues] = useState<Record<string, SchemaFormValue>>({});
	const [installing, setInstalling] = useState(false);
	const [error, setError] = useState<string | null>(null);

	// Reset per card, so a previous card's API key is never carried over.
	useEffect(() => {
		if (!card) return;
		setName(editing?.name ?? card.name);
		setValues(defaultValuesFromSchema(card.inputs_schema as JSONSchema, new Set()));
		setError(null);
	}, [card, editing]);

	const handleInstall = async () => {
		if (!card) return;
		setInstalling(true);
		setError(null);
		try {
			if (editing) {
				// Only the keys the user actually filled are sent; the
				// server merges them over what it already holds, so leaving
				// a secret field blank keeps the existing one.
				const filled = Object.fromEntries(
					Object.entries(values).filter(([, v]) => v !== undefined && v !== ''),
				);
				await mcpApi.update(editing.id, {
					name,
					...(Object.keys(filled).length > 0 ? { values: filled } : {}),
				});
			} else {
				await hubApi.mcp.install(
					card.hub_id,
					card.id,
					{ name: name || null, values },
					// A 409 on the name is expected and answered inline, so
					// the generic error toast would only duplicate it.
					{ silent: true },
				);
			}
			onInstalled();
			onOpenChange(false);
		} catch (e) {
			setError(e instanceof ApiError ? e.detail : (e as Error).message);
		} finally {
			setInstalling(false);
		}
	};

	return (
		<Dialog open={card !== null} onOpenChange={onOpenChange}>
			<DialogContent>
				<DialogHeader>
					<DialogTitle>
						{t(editing ? 'mcp.editTitle' : 'mcp.installTitle', {
							name: card?.display_name || card?.name || '',
						})}
					</DialogTitle>
					<DialogDescription>
						{t(editing ? 'mcp.editDescription' : 'mcp.installDescription')}
					</DialogDescription>
				</DialogHeader>

				<FieldGroup>
					<Field>
						<FieldLabel htmlFor="install-mcp-name">{t('mcp.nameLabel')}</FieldLabel>
						<Input
							id="install-mcp-name"
							value={name}
							onChange={(e) => setName(e.target.value)}
							placeholder={card?.name}
						/>
					</Field>
				</FieldGroup>

				{card?.auth === 'inputs' && (
					<SchemaForm
						schema={card.inputs_schema as JSONSchema}
						values={values}
						onChange={(key, value) => setValues((prev) => ({ ...prev, [key]: value }))}
						skipFields={new Set()}
						idPrefix="install-mcp"
					/>
				)}

				{error && (
					<Alert variant="destructive">
						<CircleAlert />
						<AlertDescription>{error}</AlertDescription>
					</Alert>
				)}

				<DialogFooter>
					<Button variant="outline" onClick={() => onOpenChange(false)}>
						{t('common.cancel')}
					</Button>
					<Button onClick={handleInstall} disabled={installing || !name}>
						{installing && <Spinner />}
						{t(editing ? 'common.save' : 'mcp.install')}
					</Button>
				</DialogFooter>
			</DialogContent>
		</Dialog>
	);
}
