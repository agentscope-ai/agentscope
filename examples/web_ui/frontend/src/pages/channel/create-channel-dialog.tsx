import { CircleAlert, Loader2, PlusCircle } from 'lucide-react';
import * as React from 'react';

import type { ChatModelConfig, ChannelTypeSchema, DmScope, PermissionMode } from '@/api';
import { channelApi } from '@/api';
import { ModelParametersPopover } from '@/components/popover/ModelParametersPopover';
import { LlmSelect } from '@/components/select/LlmSelect';
import { PermissionModeSelect } from '@/components/select/PermissionModeSelect';
import { Button } from '@/components/ui/button';
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from '@/components/ui/dialog';
import { Field, FieldGroup, FieldLabel } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from '@/components/ui/select';
import { Switch } from '@/components/ui/switch';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { useAgents } from '@/hooks/useAgents';
import { useAvailableModels } from '@/hooks/useAvailableModels';
import { useTranslation } from '@/i18n/useI18n';

interface Props {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	onCreated?: () => void;
}

const DM_SCOPES: { value: DmScope; labelKey: string; descKey: string }[] = [
	{
		value: 'PER_PEER',
		labelKey: 'channel.dmScopeOptions.perPeer',
		descKey: 'channel.dmScopeOptions.perPeerDesc',
	},
	{
		value: 'PER_CHAT',
		labelKey: 'channel.dmScopeOptions.perChat',
		descKey: 'channel.dmScopeOptions.perChatDesc',
	},
	{
		value: 'MAIN',
		labelKey: 'channel.dmScopeOptions.main',
		descKey: 'channel.dmScopeOptions.mainDesc',
	},
	{
		value: 'PER_CHANNEL_PEER',
		labelKey: 'channel.dmScopeOptions.perChannelPeer',
		descKey: 'channel.dmScopeOptions.perChannelPeerDesc',
	},
];

function getDefaultForm() {
	return {
		channelId: '',
		channelType: 'feishu',
		credentials: {} as Record<string, string>,
		agentId: '',
		chatModelConfig: null as ChatModelConfig | null,
		fallbackChatModelConfig: null as ChatModelConfig | null,
		dmScope: 'PER_CHAT' as DmScope,
		permissionMode: 'dont_ask' as PermissionMode,
		showToolMessages: false,
		showThinking: false,
	};
}

export function CreateChannelDialog({ open, onOpenChange, onCreated }: Props) {
	const { t } = useTranslation();
	const { agents } = useAgents();
	const { groups } = useAvailableModels();
	const [form, setForm] = React.useState(getDefaultForm);
	const [loading, setLoading] = React.useState(false);
	const [error, setError] = React.useState('');
	const [channelTypes, setChannelTypes] = React.useState<ChannelTypeSchema[]>([]);

	React.useEffect(() => {
		if (open) {
			setForm(getDefaultForm());
			setError('');
			channelApi
				.listTypes()
				.then(setChannelTypes)
				.catch(() => {});
		}
	}, [open]);

	React.useEffect(() => {
		if (agents.length > 0 && !form.agentId) {
			setForm((prev) => ({ ...prev, agentId: agents[0].id }));
		}
	}, [agents, form.agentId]);

	// Reset credentials when channel type changes
	React.useEffect(() => {
		setForm((prev) => ({ ...prev, credentials: {} }));
	}, [form.channelType]);

	const selectedTypeSchema = React.useMemo(
		() => channelTypes.find((ct) => ct.channel_type === form.channelType),
		[channelTypes, form.channelType],
	);

	const credentialFields = React.useMemo(() => {
		const schema = selectedTypeSchema?.credentials_schema;
		if (!schema || !schema.properties) return [];
		const props = schema.properties as Record<string, Record<string, unknown>>;
		const required = (schema.required as string[]) || [];
		return Object.entries(props).map(([key, def]) => ({
			key,
			title: (def.title as string) || key,
			description: def.description as string | undefined,
			format: def.format as string | undefined,
			required: required.includes(key),
		}));
	}, [selectedTypeSchema]);

	const set = <K extends keyof ReturnType<typeof getDefaultForm>>(
		key: K,
		value: ReturnType<typeof getDefaultForm>[K],
	) => setForm((prev) => ({ ...prev, [key]: value }));

	const setCredential = (key: string, value: string) => {
		setForm((prev) => ({
			...prev,
			credentials: { ...prev.credentials, [key]: value },
		}));
	};

	const selectedModelCard = React.useMemo(() => {
		if (!form.chatModelConfig) return null;
		const items = groups[form.chatModelConfig.type];
		if (!items) return null;
		for (const { models } of items) {
			const card = models.find((m) => m.name === form.chatModelConfig!.model);
			if (card) return card;
		}
		return null;
	}, [groups, form.chatModelConfig?.type, form.chatModelConfig?.model]);

	const handleParametersChange = (parameters: Record<string, unknown>) => {
		if (!form.chatModelConfig) return;
		set('chatModelConfig', { ...form.chatModelConfig, parameters });
	};

	const isValid =
		form.channelId.trim() &&
		form.channelType &&
		credentialFields.every((f) => !f.required || form.credentials[f.key]?.trim()) &&
		form.agentId &&
		form.chatModelConfig;

	const handleSubmit = async () => {
		setError('');
		if (!isValid) return;
		setLoading(true);
		try {
			await channelApi.create({
				channel_id: form.channelId.trim(),
				channel_type: form.channelType,
				credentials: form.credentials,
				default_agent_id: form.agentId,
				chat_model_config: form.chatModelConfig,
				fallback_chat_model_config: form.fallbackChatModelConfig,
				dm_scope: form.dmScope,
				permission_mode: form.permissionMode,
				filter_tool_messages: !form.showToolMessages,
				filter_thinking_messages: !form.showThinking,
				enabled: true,
			});
			onCreated?.();
			onOpenChange(false);
		} catch (e) {
			setError(String(e));
		} finally {
			setLoading(false);
		}
	};

	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogContent className="!w-[520px] !max-w-[520px]">
				<DialogHeader>
					<DialogTitle>{t('channel.create.title')}</DialogTitle>
					<DialogDescription>{t('channel.create.description')}</DialogDescription>
				</DialogHeader>

				<div className="no-scrollbar -mx-4 max-h-[75vh] overflow-y-auto px-4">
					<FieldGroup className="[&>[data-orientation=horizontal]>:last-child]:w-48">
						<Field>
							<FieldLabel>{t('channel.create.channelId')}</FieldLabel>
							<Input
								className="text-sm h-8"
								value={form.channelId}
								onChange={(e) => set('channelId', e.target.value)}
								placeholder={t('channel.create.channelIdPlaceholder')}
							/>
						</Field>

						<Field orientation="horizontal">
							<FieldLabel>{t('channel.create.channelType')}</FieldLabel>
							<Select
								value={form.channelType}
								onValueChange={(v) => {
									set('channelType', v);
									set('credentials', {});
								}}
							>
								<SelectTrigger size="sm">
									<SelectValue />
								</SelectTrigger>
								<SelectContent>
									{channelTypes.length > 0 ? (
										channelTypes.map((ct) => (
											<SelectItem
												key={ct.channel_type}
												value={ct.channel_type}
											>
												{ct.display_name}
											</SelectItem>
										))
									) : (
										<>
											<SelectItem value="feishu">Feishu</SelectItem>
											<SelectItem value="dingtalk">DingTalk</SelectItem>
											<SelectItem value="discord">Discord</SelectItem>
											<SelectItem value="wecom">WeCom</SelectItem>
										</>
									)}
								</SelectContent>
							</Select>
						</Field>

						{credentialFields.map((field) => (
							<Field key={field.key}>
								<FieldLabel>
									{field.title}
									{field.required && ' *'}
								</FieldLabel>
								<Input
									className="text-sm h-8"
									type={field.format === 'password' ? 'password' : 'text'}
									value={form.credentials[field.key] || ''}
									onChange={(e) => setCredential(field.key, e.target.value)}
									placeholder={field.description || field.title}
								/>
							</Field>
						))}

						<Field orientation="horizontal">
							<FieldLabel>{t('common.agent')}</FieldLabel>
							<Select value={form.agentId} onValueChange={(v) => set('agentId', v)}>
								<SelectTrigger className="w-full" size="sm">
									<SelectValue placeholder={t('common.selectAgent')} />
								</SelectTrigger>
								<SelectContent>
									{agents.map((agent) => (
										<SelectItem key={agent.id} value={agent.id}>
											{agent.data.name}
										</SelectItem>
									))}
								</SelectContent>
							</Select>
						</Field>

						<Field orientation="horizontal">
							<FieldLabel>{t('common.model')}</FieldLabel>
							<div className="flex items-center gap-1">
								<LlmSelect
									value={form.chatModelConfig}
									onChange={(v) => set('chatModelConfig', v)}
								/>
								<ModelParametersPopover
									selectedModel={form.chatModelConfig}
									modelCard={selectedModelCard}
									onChange={handleParametersChange}
									selectedFallbackModel={form.fallbackChatModelConfig}
									onFallbackChange={(cfg) => set('fallbackChatModelConfig', cfg)}
								/>
							</div>
						</Field>

						<Field>
							<div className="flex items-center justify-between">
								<FieldLabel>{t('channel.create.dmScope')}</FieldLabel>
								<Select
									value={form.dmScope}
									onValueChange={(v) => set('dmScope', v as DmScope)}
								>
									<SelectTrigger size="sm" className="w-48">
										<SelectValue />
									</SelectTrigger>
									<SelectContent>
										<TooltipProvider delayDuration={200}>
											{DM_SCOPES.map((s) => (
												<Tooltip key={s.value}>
													<TooltipTrigger asChild>
														<SelectItem value={s.value}>
															{t(s.labelKey)}
														</SelectItem>
													</TooltipTrigger>
													<TooltipContent side="right">
														{t(s.descKey)}
													</TooltipContent>
												</Tooltip>
											))}
										</TooltipProvider>
									</SelectContent>
								</Select>
							</div>
							<span className="text-xs text-muted-foreground">
								{t(DM_SCOPES.find((s) => s.value === form.dmScope)?.descKey ?? '')}
							</span>
						</Field>

						<Field orientation="horizontal">
							<FieldLabel>{t('channel.create.permissionMode')}</FieldLabel>
							<PermissionModeSelect
								className="w-full"
								value={form.permissionMode}
								onChange={(v) => set('permissionMode', v)}
							/>
						</Field>

						<Field>
							<div className="flex flex-row items-center justify-between">
								<div className="flex flex-col gap-y-0.5">
									<FieldLabel>{t('channel.create.showToolMessages')}</FieldLabel>
									<span className="text-xs text-muted-foreground">
										{t('channel.create.showToolMessagesDesc')}
									</span>
								</div>
								<Switch
									checked={form.showToolMessages}
									onCheckedChange={(v) => set('showToolMessages', v)}
								/>
							</div>
						</Field>

						<Field>
							<div className="flex flex-row items-center justify-between">
								<div className="flex flex-col gap-y-0.5">
									<FieldLabel>{t('channel.create.showThinking')}</FieldLabel>
									<span className="text-xs text-muted-foreground">
										{t('channel.create.showThinkingDesc')}
									</span>
								</div>
								<Switch
									checked={form.showThinking}
									onCheckedChange={(v) => set('showThinking', v)}
								/>
							</div>
						</Field>

						{error && <p className="text-sm text-destructive">{error}</p>}
					</FieldGroup>
				</div>

				<DialogFooter>
					<Button variant="ghost" onClick={() => onOpenChange(false)} disabled={loading}>
						<CircleAlert className="size-3.5" />
						{t('common.cancel')}
					</Button>
					<Button onClick={handleSubmit} disabled={loading || !isValid}>
						{loading ? (
							<Loader2 className="size-3.5 animate-spin" />
						) : (
							<PlusCircle className="size-3.5" />
						)}
						{loading ? t('common.creating') : t('common.create')}
					</Button>
				</DialogFooter>
			</DialogContent>
		</Dialog>
	);
}
