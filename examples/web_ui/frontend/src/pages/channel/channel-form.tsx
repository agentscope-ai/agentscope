import * as React from 'react';

import type {
	AgentView,
	ChannelBinding,
	ChannelRecord,
	ChannelTypeSchema,
	ChatModelConfig,
	CreateChannelRequest,
	PermissionMode,
	UpdateChannelRequest,
} from '@/api';
import { ModelParametersPopover } from '@/components/popover/ModelParametersPopover';
import { LlmSelect } from '@/components/select/LlmSelect';
import { PermissionModeSelect } from '@/components/select/PermissionModeSelect';
import {
	Field,
	FieldContent,
	FieldDescription,
	FieldGroup,
	FieldLabel,
} from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { Separator } from '@/components/ui/separator';
import { Switch } from '@/components/ui/switch';
import { useAvailableModels } from '@/hooks/useAvailableModels';
import { useTranslation } from '@/i18n/useI18n';
import { BindingsEditor } from '@/pages/channel/bindings-editor';

export interface ChannelFormValue {
	channelType: string;
	name: string;
	credentials: Record<string, string>;
	platformConfig: Record<string, unknown>;
	bindings: ChannelBinding[];
	chatModelConfig: ChatModelConfig | null;
	fallbackChatModelConfig: ChatModelConfig | null;
	permissionMode: PermissionMode;
}

export function defaultChannelForm(agentId = ''): ChannelFormValue {
	return {
		channelType: 'feishu',
		name: '',
		credentials: {},
		platformConfig: {},
		bindings: [
			{
				match_key: 'chat_id',
				match_value: '*',
				agent_id: agentId,
				session_scope: 'per_chat',
			},
		],
		chatModelConfig: null,
		fallbackChatModelConfig: null,
		permissionMode: 'default' as PermissionMode,
	};
}

export function channelFormFromRecord(record: ChannelRecord): ChannelFormValue {
	return {
		channelType: record.channel_type,
		name: record.name ?? '',
		credentials: {},
		platformConfig: record.platform_config ?? {},
		bindings: record.routing.bindings,
		chatModelConfig: record.session.chat_model_config,
		fallbackChatModelConfig: record.session.fallback_chat_model_config ?? null,
		permissionMode: record.session.permission_mode,
	};
}

function sessionSettings(v: ChannelFormValue) {
	return {
		session: {
			chat_model_config: v.chatModelConfig as ChatModelConfig,
			fallback_chat_model_config: v.fallbackChatModelConfig,
			permission_mode: v.permissionMode,
		},
	};
}

export function toCreateRequest(v: ChannelFormValue): CreateChannelRequest {
	return {
		channel_type: v.channelType,
		name: v.name.trim() || null,
		credentials: v.credentials,
		platform_config: v.platformConfig,
		routing: { bindings: v.bindings },
		enabled: true,
		...sessionSettings(v),
	};
}

export function toUpdateRequest(v: ChannelFormValue): UpdateChannelRequest {
	return {
		name: v.name.trim() || null,
		platform_config: v.platformConfig,
		routing: { bindings: v.bindings },
		...sessionSettings(v),
	};
}

interface Props {
	value: ChannelFormValue;
	onChange: (value: ChannelFormValue) => void;
	agents: AgentView[];
	channelTypes: ChannelTypeSchema[];
	/** Create mode exposes type + credential fields; edit mode locks them. */
	mode: 'create' | 'edit';
}

export function ChannelForm({ value, onChange, agents, channelTypes, mode }: Props) {
	const { t } = useTranslation();
	const { groups } = useAvailableModels();

	const set = <K extends keyof ChannelFormValue>(key: K, v: ChannelFormValue[K]) =>
		onChange({ ...value, [key]: v });

	const typeSchema = React.useMemo(
		() => channelTypes.find((ct) => ct.channel_type === value.channelType),
		[channelTypes, value.channelType],
	);

	const credentialFields = React.useMemo(() => {
		const schema = typeSchema?.credentials_schema as
			| { properties?: Record<string, Record<string, unknown>>; required?: string[] }
			| undefined;
		if (!schema?.properties) return [];
		const required = schema.required ?? [];
		return Object.entries(schema.properties).map(([key, def]) => ({
			key,
			title: t(`channel.fields.${key}.title`, {
				defaultValue: (def.title as string) || key,
			}),
			description:
				t(`channel.fields.${key}.description`, {
					defaultValue: (def.description as string) || '',
				}) || undefined,
			format: def.format as string | undefined,
			required: required.includes(key),
		}));
	}, [t, typeSchema]);

	const configFields = React.useMemo(() => {
		const schema = typeSchema?.config_schema as
			| { properties?: Record<string, Record<string, unknown>> }
			| undefined;
		if (!schema?.properties) return [];
		return Object.entries(schema.properties).map(([key, def]) => ({
			key,
			title: t(`channel.fields.${key}.title`, {
				defaultValue: (def.title as string) || key,
			}),
			description:
				t(`channel.fields.${key}.description`, {
					defaultValue: (def.description as string) || '',
				}) || undefined,
			type: def.type as string | undefined,
			default: def.default,
		}));
	}, [t, typeSchema]);

	const selectedModelCard = React.useMemo(() => {
		if (!value.chatModelConfig) return null;
		const items = groups[value.chatModelConfig.type];
		if (!items) return null;
		for (const { models } of items) {
			const card = models.find((m) => m.name === value.chatModelConfig!.model);
			if (card) return card;
		}
		return null;
	}, [groups, value.chatModelConfig?.type, value.chatModelConfig?.model]);

	return (
		<FieldGroup className="[&>[data-orientation=horizontal]>:last-child]:w-48">
			{mode === 'edit' && (
				<Field orientation="horizontal">
					<FieldLabel>{t('channel.create.channelType')}</FieldLabel>
					<span className="text-sm">{typeSchema?.display_name ?? value.channelType}</span>
				</Field>
			)}

			<Field orientation="horizontal">
				<FieldContent>
					<FieldLabel>{t('channel.create.nameLabel')}</FieldLabel>
					<FieldDescription className="text-xs">
						{t('channel.create.nameDesc')}
					</FieldDescription>
				</FieldContent>
				<Input
					className="text-sm"
					value={value.name}
					onChange={(e) => set('name', e.target.value)}
					placeholder={t('channel.create.namePlaceholder')}
				/>
			</Field>

			{mode === 'create' &&
				credentialFields.map((field) => (
					<Field key={field.key}>
						<FieldLabel>
							{field.title}
							{field.required && ' *'}
						</FieldLabel>
						<Input
							className="text-sm"
							type={field.format === 'password' ? 'password' : 'text'}
							value={value.credentials[field.key] || ''}
							onChange={(e) =>
								set('credentials', {
									...value.credentials,
									[field.key]: e.target.value,
								})
							}
							placeholder={field.description || field.title}
						/>
					</Field>
				))}

			<Separator />

			<Field orientation="horizontal">
				<FieldContent>
					<FieldLabel>{t('common.model')}</FieldLabel>
					<FieldDescription className="text-xs">
						{t('channel.create.modelDesc')}
					</FieldDescription>
				</FieldContent>
				<div className="flex w-full items-center gap-1">
					<LlmSelect
						size="default"
						className="min-w-0 flex-1"
						value={value.chatModelConfig}
						onChange={(v) => set('chatModelConfig', v)}
					/>
					<ModelParametersPopover
						selectedModel={value.chatModelConfig}
						modelCard={selectedModelCard}
						onChange={(parameters) =>
							value.chatModelConfig &&
							set('chatModelConfig', { ...value.chatModelConfig, parameters })
						}
						selectedFallbackModel={value.fallbackChatModelConfig}
						onFallbackChange={(cfg) => set('fallbackChatModelConfig', cfg)}
					/>
				</div>
			</Field>

			<Field orientation="horizontal">
				<FieldContent>
					<FieldLabel>{t('channel.create.permissionMode')}</FieldLabel>
					<FieldDescription className="text-xs">
						{t('channel.create.permissionModeDesc')}
					</FieldDescription>
				</FieldContent>
				<PermissionModeSelect
					size="default"
					className="w-full"
					value={value.permissionMode}
					onChange={(v) => set('permissionMode', v)}
				/>
			</Field>

			<Separator />

			<Field>
				<FieldLabel>{t('channel.routing')}</FieldLabel>
				<span className="mb-1 text-xs text-muted-foreground">
					{t('channel.routingDesc')}
				</span>
				<BindingsEditor
					value={value.bindings}
					onChange={(b) => set('bindings', b)}
					agents={agents}
				/>
			</Field>

			<Separator />

			{configFields.map((field) => (
				<Field key={field.key}>
					<div className="flex flex-row items-center justify-between gap-4">
						<div className="flex flex-col gap-y-0.5">
							<FieldLabel>{field.title}</FieldLabel>
							{field.description && (
								<span className="text-xs text-muted-foreground">
									{field.description}
								</span>
							)}
						</div>
						{field.type === 'boolean' ? (
							<Switch
								checked={
									(value.platformConfig[field.key] as boolean) ??
									(field.default as boolean) ??
									false
								}
								onCheckedChange={(v) =>
									set('platformConfig', {
										...value.platformConfig,
										[field.key]: v,
									})
								}
							/>
						) : (
							<Input
								className="h-8 w-48 text-sm"
								value={String(
									value.platformConfig[field.key] ?? field.default ?? '',
								)}
								onChange={(e) =>
									set('platformConfig', {
										...value.platformConfig,
										[field.key]: e.target.value,
									})
								}
							/>
						)}
					</div>
				</Field>
			))}
		</FieldGroup>
	);
}

export function isChannelFormValid(v: ChannelFormValue, mode: 'create' | 'edit'): boolean {
	if (!v.name.trim()) return false;
	if (!v.chatModelConfig) return false;
	if (v.bindings.length === 0) return false;
	if (v.bindings.some((b) => !b.agent_id)) return false;
	if (mode === 'create' && !v.channelType) return false;
	return true;
}
