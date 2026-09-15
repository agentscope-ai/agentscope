import { CircleAlert } from 'lucide-react';
import { useState, useCallback } from 'react';

import type { MCPClient, StdioMCPConfig, HttpMCPConfig } from '@/api/types';
import { Alert, AlertDescription } from '@/components/ui/alert.tsx';
import { Checkbox } from '@/components/ui/checkbox.tsx';
import {
	Field,
	FieldContent,
	FieldDescription,
	FieldGroup,
	FieldLabel,
	FieldSet,
} from '@/components/ui/field.tsx';
import { InputGroup, InputGroupTextarea } from '@/components/ui/input-group.tsx';
import { useTranslation } from '@/i18n/useI18n.ts';

function parseMcpConfig(
	raw: string,
	keepAlive: boolean,
	t: (key: string, opts?: Record<string, string>) => string,
): MCPClient[] {
	let parsed: unknown;
	try {
		parsed = JSON.parse(raw);
	} catch (e) {
		throw new Error(t('dialog-mcp-create.parseError', { message: (e as Error).message }));
	}

	const obj = parsed as Record<string, unknown>;
	const servers = obj.mcpServers as Record<string, Record<string, unknown>> | undefined;
	if (!servers || typeof servers !== 'object') {
		throw new Error(t('dialog-mcp-create.missingMcpServers'));
	}

	const entries = Object.entries(servers);
	if (entries.length === 0) {
		throw new Error(t('dialog-mcp-create.emptyMcpServers'));
	}

	return entries.map(([name, config]) => {
		let mcp_config: StdioMCPConfig | HttpMCPConfig;
		if ('url' in config) {
			mcp_config = {
				type: 'http_mcp',
				url: config.url as string,
				headers: (config.headers as Record<string, string> | undefined) ?? null,
				timeout: (config.timeout as number | undefined) ?? null,
			};
		} else {
			mcp_config = {
				type: 'stdio_mcp',
				command: config.command as string,
				args: (config.args as string[] | undefined) ?? null,
				env: (config.env as Record<string, string> | undefined) ?? null,
				cwd: (config.cwd as string | undefined) ?? null,
			};
		}
		return { name, is_stateful: keepAlive, mcp_config };
	});
}

interface FormProps {
	/**
	 * Ties this form to a submit button rendered outside it. The dialog's
	 * footer has to sit outside the tabs to keep its layout fixed, and the
	 * `form` attribute is how a button reaches a form it is not inside.
	 */
	formId: string;
	onAdd: (mcps: MCPClient[]) => Promise<void>;
	/** Called once the add succeeds, e.g. to close the surrounding dialog. */
	onDone?: () => void;
	/** Reports work in flight, so the outside button can show it. */
	onBusyChange?: (busy: boolean) => void;
}

/**
 * The paste-a-config form — fields only; the submit button belongs to
 * whoever renders it.
 */
export const MCPConfigForm = ({ formId, onAdd, onDone, onBusyChange }: FormProps) => {
	const { t } = useTranslation();
	const [configValue, setConfigValue] = useState('');
	const [keepAlive, setKeepAlive] = useState(true);
	const [errorMsg, setErrorMsg] = useState('');

	const handleAdd = useCallback(async () => {
		setErrorMsg('');
		onBusyChange?.(true);
		let mcpClients: MCPClient[];
		try {
			mcpClients = parseMcpConfig(configValue, keepAlive, t);
		} catch (e) {
			setErrorMsg((e as Error).message);
			onBusyChange?.(false);
			return;
		}

		try {
			await onAdd(mcpClients);
			onBusyChange?.(false);
			setTimeout(() => onDone?.(), 1500);
		} catch (e) {
			// ApiErrors are already shown via the global toast in client.ts.
			// Show only local validation errors (e.g. duplicate name) inline.
			const isApiError = e instanceof Error && e.name === 'ApiError';
			if (!isApiError) {
				setErrorMsg(e instanceof Error ? e.message : String(e));
			}
			onBusyChange?.(false);
		}
	}, [configValue, keepAlive, t, onAdd, onDone, onBusyChange]);

	return (
		<form
			id={formId}
			onSubmit={(e) => {
				e.preventDefault();
				handleAdd();
			}}
		>
			<FieldSet>
				<FieldGroup>
					<Field>
						<FieldContent>
							<FieldLabel>{t('dialog-mcp-create.configLabel')}</FieldLabel>
						</FieldContent>
						<InputGroup>
							<InputGroupTextarea
								className="max-h-100"
								value={configValue}
								onChange={(e) => setConfigValue(e.target.value)}
								placeholder={
									'{\n  "mcpServers": {\n    "playwright": {\n      "command": "npx",\n      "args": ["@playwright/mcp@latest"]\n    }\n  }\n}'
								}
							/>
						</InputGroup>
					</Field>
					<Field orientation="horizontal">
						<Checkbox
							id="mcp-keep-alive"
							checked={keepAlive}
							onCheckedChange={(v) => setKeepAlive(v === true)}
						/>
						<FieldContent>
							<FieldLabel htmlFor="mcp-keep-alive">
								{t('dialog-mcp-create.keepAlive')}
							</FieldLabel>
							<FieldDescription>
								{t('dialog-mcp-create.keepAliveDesc')}
							</FieldDescription>
						</FieldContent>
					</Field>
				</FieldGroup>
			</FieldSet>
			{errorMsg && (
				<Alert variant="destructive">
					<CircleAlert />
					<AlertDescription>{errorMsg}</AlertDescription>
				</Alert>
			)}
		</form>
	);
};
