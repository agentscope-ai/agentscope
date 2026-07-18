import { Loader2, Plus, Trash2 } from 'lucide-react';
import * as React from 'react';

import type { ChatModelConfig, ChannelRecord, DmScope, PermissionMode, RoutingRule } from '@/api';
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
	channel: ChannelRecord | null;
	open: boolean;
	onOpenChange: (open: boolean) => void;
	onUpdated?: () => void;
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

export function EditChannelDialog({ channel, open, onOpenChange, onUpdated }: Props) {
	const { t } = useTranslation();
	const { agents } = useAgents();
	const { groups } = useAvailableModels();
	const [loading, setLoading] = React.useState(false);
	const [error, setError] = React.useState('');

	const [agentId, setAgentId] = React.useState('');
	const [chatModelConfig, setChatModelConfig] = React.useState<ChatModelConfig | null>(null);
	const [fallbackChatModelConfig, setFallbackChatModelConfig] =
		React.useState<ChatModelConfig | null>(null);
	const [dmScope, setDmScope] = React.useState<DmScope>('PER_PEER');
	const [permissionMode, setPermissionMode] = React.useState<PermissionMode>('dont_ask');
	const [showToolMessages, setShowToolMessages] = React.useState(false);
	const [showThinking, setShowThinking] = React.useState(false);
	const [routingRules, setRoutingRules] = React.useState<RoutingRule[]>([]);
	const [newRule, setNewRule] = React.useState<Partial<RoutingRule>>({
		metadata_key: 'chat_id',
		metadata_value: '',
		agent_id: '',
		priority: 0,
	});
	const [showAddRule, setShowAddRule] = React.useState(false);
	const [knownChats, setKnownChats] = React.useState<
		{ chat_id: string; name: string; source: string }[]
	>([]);

	React.useEffect(() => {
		if (channel && open) {
			channelApi
				.listChatIds(channel.channel_id)
				.then(setKnownChats)
				.catch(() => {});
		}
	}, [channel, open]);

	React.useEffect(() => {
		if (channel && open) {
			setAgentId(channel.default_agent_id);
			setChatModelConfig(channel.chat_model_config ?? null);
			setFallbackChatModelConfig(channel.fallback_chat_model_config ?? null);
			setDmScope((channel.dm_scope ?? 'PER_PEER') as DmScope);
			setPermissionMode((channel.permission_mode ?? 'dont_ask') as PermissionMode);
			setShowToolMessages(!(channel.filter_tool_messages ?? true));
			setShowThinking(!(channel.filter_thinking_messages ?? true));
			setRoutingRules(channel.routing_rules ?? []);
			setShowAddRule(false);
			setNewRule({ metadata_key: 'chat_id', metadata_value: '', agent_id: '', priority: 0 });
			setError('');
		}
	}, [channel, open]);

	const selectedModelCard = React.useMemo(() => {
		if (!chatModelConfig) return null;
		const items = groups[chatModelConfig.type];
		if (!items) return null;
		for (const { models } of items) {
			const card = models.find((m) => m.name === chatModelConfig.model);
			if (card) return card;
		}
		return null;
	}, [groups, chatModelConfig?.type, chatModelConfig?.model]);

	const handleAddRule = () => {
		if (!newRule.metadata_value || !newRule.agent_id) return;
		setRoutingRules([...routingRules, newRule as RoutingRule]);
		setNewRule({ metadata_key: 'chat_id', metadata_value: '', agent_id: '', priority: 0 });
		setShowAddRule(false);
	};

	const handleRemoveRule = (idx: number) => {
		setRoutingRules(routingRules.filter((_, i) => i !== idx));
	};

	const handleSubmit = async () => {
		if (!channel) return;
		setError('');
		setLoading(true);
		try {
			await channelApi.update(channel.channel_id, {
				default_agent_id: agentId,
				chat_model_config: chatModelConfig ?? undefined,
				fallback_chat_model_config: fallbackChatModelConfig,
				dm_scope: dmScope,
				permission_mode: permissionMode,
				filter_tool_messages: !showToolMessages,
				filter_thinking_messages: !showThinking,
				routing_rules: routingRules,
			});
			onOpenChange(false);
			onUpdated?.();
		} catch (e: unknown) {
			setError(e instanceof Error ? e.message : String(e));
		} finally {
			setLoading(false);
		}
	};

	if (!channel) return null;

	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogContent className="max-w-md">
				<DialogHeader>
					<DialogTitle>
						{t('common.edit')}: {channel.channel_id}
					</DialogTitle>
					<DialogDescription>
						{channel.channel_type.charAt(0).toUpperCase() +
							channel.channel_type.slice(1)}
					</DialogDescription>
				</DialogHeader>

				<FieldGroup>
					<Field orientation="horizontal">
						<FieldLabel>{t('common.agent')}</FieldLabel>
						<Select value={agentId} onValueChange={setAgentId}>
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
							<LlmSelect value={chatModelConfig} onChange={setChatModelConfig} />
							<ModelParametersPopover
								selectedModel={chatModelConfig}
								modelCard={selectedModelCard}
								selectedFallbackModel={fallbackChatModelConfig}
								onFallbackChange={setFallbackChatModelConfig}
								onChange={(params) => {
									if (chatModelConfig) {
										setChatModelConfig({
											...chatModelConfig,
											parameters: params,
										});
									}
								}}
							/>
						</div>
					</Field>

					<Field>
						<div className="flex items-center justify-between">
							<FieldLabel>{t('channel.create.dmScope')}</FieldLabel>
							<Select value={dmScope} onValueChange={(v) => setDmScope(v as DmScope)}>
								<SelectTrigger className="w-auto" size="sm">
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
							{t(DM_SCOPES.find((s) => s.value === dmScope)?.descKey ?? '')}
						</span>
					</Field>

					<Field orientation="horizontal">
						<FieldLabel>{t('channel.create.permissionMode')}</FieldLabel>
						<PermissionModeSelect value={permissionMode} onChange={setPermissionMode} />
					</Field>

					<Field>
						<div className="flex items-center justify-between">
							<FieldLabel>{t('channel.create.showToolMessages')}</FieldLabel>
							<Switch
								checked={showToolMessages}
								onCheckedChange={setShowToolMessages}
							/>
						</div>
					</Field>

					<Field>
						<div className="flex items-center justify-between">
							<FieldLabel>{t('channel.create.showThinking')}</FieldLabel>
							<Switch checked={showThinking} onCheckedChange={setShowThinking} />
						</div>
					</Field>

					{/* Routing Rules */}
					<Field>
						<div className="flex items-center justify-between">
							<FieldLabel>{t('channel.routingRules')}</FieldLabel>
							<Button
								size="icon-sm"
								variant="ghost"
								onClick={() => setShowAddRule(!showAddRule)}
							>
								<Plus className="size-3.5" />
							</Button>
						</div>
						{routingRules.length > 0 && (
							<div className="space-y-1.5 mt-1">
								{routingRules.map((rule, idx) => {
									const displayValue =
										rule.metadata_key === 'chat_type'
											? rule.metadata_value === 'p2p'
												? t('channel.routingRules.chatTypeP2p')
												: t('channel.routingRules.chatTypeGroup')
											: rule.metadata_value;
									return (
										<div
											key={idx}
											className="flex items-center gap-2 text-xs bg-muted/50 rounded px-2 py-1.5"
										>
											<span className="text-muted-foreground">
												{rule.metadata_key}=
											</span>
											<span className="font-mono truncate max-w-[100px]">
												{displayValue}
											</span>
											<span className="text-muted-foreground">→</span>
											<span className="truncate max-w-[80px]">
												{agents.find((a) => a.id === rule.agent_id)?.data
													.name ?? rule.agent_id.slice(0, 8)}
											</span>
											<Button
												size="icon-sm"
												variant="ghost"
												className="ml-auto text-destructive size-5"
												onClick={() => handleRemoveRule(idx)}
											>
												<Trash2 className="size-3" />
											</Button>
										</div>
									);
								})}
							</div>
						)}
						{showAddRule && (
							<div className="mt-2 space-y-2 border rounded p-2">
								<div className="flex gap-2">
									<Select
										value={newRule.metadata_key}
										onValueChange={(v) =>
											setNewRule({
												...newRule,
												metadata_key: v,
												metadata_value: '',
											})
										}
									>
										<SelectTrigger className="w-[100px]" size="sm">
											<SelectValue />
										</SelectTrigger>
										<SelectContent>
											<TooltipProvider delayDuration={200}>
												<Tooltip>
													<TooltipTrigger asChild>
														<SelectItem value="chat_id">
															chat_id
														</SelectItem>
													</TooltipTrigger>
													<TooltipContent side="right">
														{t('channel.routingRules.chatIdDesc')}
													</TooltipContent>
												</Tooltip>
												<Tooltip>
													<TooltipTrigger asChild>
														<SelectItem value="chat_type">
															chat_type
														</SelectItem>
													</TooltipTrigger>
													<TooltipContent side="right">
														{t('channel.routingRules.chatTypeDesc')}
													</TooltipContent>
												</Tooltip>
											</TooltipProvider>
										</SelectContent>
									</Select>
									{newRule.metadata_key === 'chat_type' ? (
										<Select
											value={newRule.metadata_value}
											onValueChange={(v) =>
												setNewRule({ ...newRule, metadata_value: v })
											}
										>
											<SelectTrigger className="flex-1" size="sm">
												<SelectValue
													placeholder={t(
														'channel.routingRules.selectChatType',
													)}
												/>
											</SelectTrigger>
											<SelectContent>
												<SelectItem value="p2p">
													{t('channel.routingRules.chatTypeP2p')}
												</SelectItem>
												<SelectItem value="group">
													{t('channel.routingRules.chatTypeGroup')}
												</SelectItem>
											</SelectContent>
										</Select>
									) : knownChats.length > 0 ? (
										<div className="flex-1 flex gap-1">
											<Select
												value={
													knownChats.some(
														(c) =>
															c.chat_id ===
															(newRule.metadata_value ?? ''),
													)
														? newRule.metadata_value
														: '__custom__'
												}
												onValueChange={(v) => {
													setNewRule({
														...newRule,
														metadata_value: v === '__custom__' ? '' : v,
													});
												}}
											>
												<SelectTrigger className="flex-1" size="sm">
													<SelectValue
														placeholder={t(
															'channel.routingRules.selectChatId',
														)}
													/>
												</SelectTrigger>
												<SelectContent>
													{knownChats.map((chat) => (
														<SelectItem
															key={chat.chat_id}
															value={chat.chat_id}
														>
															<span className="font-mono text-xs">
																{chat.name
																	? `${chat.name} (${chat.chat_id.length > 12 ? chat.chat_id.slice(0, 6) + '…' + chat.chat_id.slice(-4) : chat.chat_id})`
																	: chat.chat_id.length > 20
																		? `${chat.chat_id.slice(0, 10)}…${chat.chat_id.slice(-8)}`
																		: chat.chat_id}
															</span>
														</SelectItem>
													))}
													<SelectItem value="__custom__">
														{t('channel.routingRules.customValue')}
													</SelectItem>
												</SelectContent>
											</Select>
											{(!newRule.metadata_value ||
												!knownChats.some(
													(c) => c.chat_id === newRule.metadata_value,
												)) && (
												<Input
													placeholder={t(
														'channel.routingRules.valuePlaceholder',
													)}
													className="flex-1 h-8 text-xs"
													value={newRule.metadata_value ?? ''}
													onChange={(e) =>
														setNewRule({
															...newRule,
															metadata_value: e.target.value,
														})
													}
												/>
											)}
										</div>
									) : (
										<Input
											placeholder={t('channel.routingRules.valuePlaceholder')}
											className="flex-1 h-8 text-xs"
											value={newRule.metadata_value ?? ''}
											onChange={(e) =>
												setNewRule({
													...newRule,
													metadata_value: e.target.value,
												})
											}
										/>
									)}
								</div>
								<div className="flex gap-2 items-center">
									<Select
										value={newRule.agent_id}
										onValueChange={(v) =>
											setNewRule({ ...newRule, agent_id: v })
										}
									>
										<SelectTrigger className="flex-1" size="sm">
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
									<Button
										size="sm"
										onClick={handleAddRule}
										disabled={!newRule.metadata_value || !newRule.agent_id}
									>
										{t('common.add')}
									</Button>
								</div>
							</div>
						)}
					</Field>
				</FieldGroup>

				{error && <p className="text-sm text-destructive">{error}</p>}

				<DialogFooter>
					<Button variant="outline" onClick={() => onOpenChange(false)}>
						{t('common.cancel')}
					</Button>
					<Button onClick={handleSubmit} disabled={loading || !agentId}>
						{loading && <Loader2 className="animate-spin" />}
						{t('common.save')}
					</Button>
				</DialogFooter>
			</DialogContent>
		</Dialog>
	);
}
