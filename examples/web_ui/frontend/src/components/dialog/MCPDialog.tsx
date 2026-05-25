import { PlusCircle, Loader2, Check } from 'lucide-react';
import { CircleAlert } from 'lucide-react';
import { useState, useCallback } from 'react';
import type { ReactNode } from 'react';

import type { MCPClient, StdioMCPConfig, HttpMCPConfig } from '@/api/types';
import { Alert, AlertDescription } from '@/components/ui/alert.tsx';
import { Button } from '@/components/ui/button.tsx';
import { Checkbox } from '@/components/ui/checkbox.tsx';
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
	DialogTrigger,
} from '@/components/ui/dialog.tsx';
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

type Status = 'idle' | 'loading' | 'success' | 'error';

interface Props {
	children: ReactNode;
	onAdd: (mcps: MCPClient[]) => Promise<void>;
}

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

export const CreateMCPDialog = ({ children, onAdd }: Props) => {
	const { t } = useTranslation();
	const [open, setOpen] = useState(false);
	const [configValue, setConfigValue] = useState('');
	const [keepAlive, setKeepAlive] = useState(true);
	const [status, setStatus] = useState<Status>('idle');
	const [errorMsg, setErrorMsg] = useState('');

	const reset = useCallback(() => {
		setConfigValue('');
		setKeepAlive(true);
		setStatus('idle');
		setErrorMsg('');
	}, []);

	const handleOpenChange = useCallback(
		(next: boolean) => {
			if (!next) reset();
			setOpen(next);
		},
		[reset],
	);

	const handleAdd = useCallback(async () => {
		setErrorMsg('');
		let mcpClients: MCPClient[];
		try {
			mcpClients = parseMcpConfig(configValue, keepAlive, t);
		} catch (e) {
			setErrorMsg((e as Error).message);
			setStatus('error');
			return;
		}

		setStatus('loading');
		try {
			await onAdd(mcpClients);
			setStatus('success');
			setTimeout(() => {
				handleOpenChange(false);
			}, 1500);
		} catch (e) {
			// ApiErrors are already shown via the global toast in client.ts.
			// Show only local validation errors (e.g. duplicate name) inline.
			const isApiError = e instanceof Error && e.name === 'ApiError';
			if (!isApiError) {
				setErrorMsg(e instanceof Error ? e.message : String(e));
			}
			setStatus('idle');
		}
	}, [configValue, keepAlive, t, onAdd, handleOpenChange]);

	return (
		<Dialog open={open} onOpenChange={handleOpenChange}>
			<DialogTrigger asChild>{children}</DialogTrigger>
			<DialogContent>
				<DialogHeader>
					<DialogTitle>{t('dialog-mcp-create.title')}</DialogTitle>
					<DialogDescription>{t('dialog-mcp-create.description')}</DialogDescription>
				</DialogHeader>
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
				<DialogFooter>
					<Button
						onClick={handleAdd}
						disabled={status === 'loading' || status === 'success'}
					>
						{status === 'loading' && <Loader2 className="animate-spin" />}
						{status === 'success' && <Check />}
						{status !== 'loading' && status !== 'success' && <PlusCircle />}
						{status === 'loading'
							? t('dialog-mcp-create.adding')
							: status === 'success'
								? t('dialog-mcp-create.added')
								: t('common.add')}
					</Button>
					<Button variant="ghost" onClick={() => handleOpenChange(false)}>
						{t('common.cancel')}
					</Button>
				</DialogFooter>
			</DialogContent>
		</Dialog>
	);
};
