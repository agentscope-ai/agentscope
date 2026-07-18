import { Cable, Circle, Pencil, Plus, Power, PowerOff, Trash2 } from 'lucide-react';
import * as React from 'react';

import type { ChannelRecord } from '@/api';
import { DeleteDialog } from '@/components/dialog/DeleteDialog';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from '@/components/ui/empty';
import { Skeleton } from '@/components/ui/skeleton';
import { useAgents } from '@/hooks/useAgents';
import { useChannels } from '@/hooks/useChannels';
import { useTranslation } from '@/i18n/useI18n';
import { CreateChannelDialog } from '@/pages/channel/create-channel-dialog';
import { EditChannelDialog } from '@/pages/channel/edit-channel-dialog';

function ChannelTypeLabel({ type }: { type: string }) {
	const labels: Record<string, string> = {
		feishu: 'Feishu',
		dingtalk: 'DingTalk',
		discord: 'Discord',
		wecom: 'WeCom',
	};
	return <span>{labels[type] ?? type}</span>;
}

function ChannelCard({
	channel,
	agentName,
	onEnable,
	onDisable,
	onEdit,
	onDelete,
}: {
	channel: ChannelRecord;
	agentName: string;
	onEnable: () => void;
	onDisable: () => void;
	onEdit: () => void;
	onDelete: () => void;
}) {
	const { t } = useTranslation();

	return (
		<Card className="shadow-sm hover:shadow transition-shadow">
			<CardHeader className="pb-3">
				<div className="flex items-center justify-between">
					<CardTitle className="text-sm font-semibold flex items-center gap-2">
						<Cable className="size-4 text-muted-foreground" />
						{channel.channel_id}
					</CardTitle>
					<div className="flex items-center gap-1">
						{channel.enabled ? (
							<Button
								size="icon-sm"
								variant="ghost"
								onClick={onDisable}
								tooltip={t('channel.disable')}
							>
								<PowerOff className="size-3.5" />
							</Button>
						) : (
							<Button
								size="icon-sm"
								variant="ghost"
								onClick={onEnable}
								tooltip={t('channel.enable')}
							>
								<Power className="size-3.5" />
							</Button>
						)}
						<Button
							size="icon-sm"
							variant="ghost"
							onClick={onEdit}
							tooltip={t('common.edit')}
						>
							<Pencil className="size-3.5" />
						</Button>
						<Button
							size="icon-sm"
							variant="ghost"
							className="text-destructive"
							onClick={onDelete}
							tooltip={t('common.delete')}
						>
							<Trash2 className="size-3.5" />
						</Button>
					</div>
				</div>
			</CardHeader>
			<CardContent className="flex flex-col gap-2 text-sm">
				<div className="flex justify-between items-center">
					<span className="text-muted-foreground">{t('channel.type')}</span>
					<ChannelTypeLabel type={channel.channel_type} />
				</div>
				<div className="flex justify-between items-center">
					<span className="text-muted-foreground">{t('common.agent')}</span>
					<span className="truncate max-w-[120px]">{agentName}</span>
				</div>
				<div className="flex justify-between items-center">
					<span className="text-muted-foreground">{t('channel.dmScope')}</span>
					<span>{channel.dm_scope}</span>
				</div>
				<div className="flex justify-between items-center">
					<span className="text-muted-foreground">{t('channel.status')}</span>
					<Badge variant={channel.enabled ? 'default' : 'secondary'} className="text-xs">
						<Circle
							className={`size-2 ${channel.enabled ? 'fill-green-400 text-green-400' : 'fill-gray-400 text-gray-400'}`}
						/>
						{channel.enabled ? t('channel.connected') : t('common.disabled')}
					</Badge>
				</div>
				{channel.chat_model_config && (
					<div className="flex justify-between items-center">
						<span className="text-muted-foreground">{t('common.model')}</span>
						<span className="truncate max-w-[140px] text-xs font-mono">
							{channel.chat_model_config.model}
						</span>
					</div>
				)}
			</CardContent>
		</Card>
	);
}

export function ChannelPage() {
	const { t } = useTranslation();
	const { channels, loading, refetch, enable, disable, remove } = useChannels();
	const { agents } = useAgents();
	const [createOpen, setCreateOpen] = React.useState(false);
	const [editTarget, setEditTarget] = React.useState<ChannelRecord | null>(null);
	const [deleteTarget, setDeleteTarget] = React.useState<ChannelRecord | null>(null);

	const getAgentName = (agentId: string) => {
		const agent = agents.find((a) => a.id === agentId);
		return agent?.data.name ?? agentId.slice(0, 8);
	};

	return (
		<div className="w-full h-full flex flex-col bg-sidebar overflow-hidden">
			<div className="flex items-center justify-between p-4 flex-shrink-0">
				<span className="text-2xl font-semibold">{t('channel.title')}</span>
				<Button size="icon-sm" onClick={() => setCreateOpen(true)}>
					<Plus />
				</Button>
			</div>

			<div className="flex-1 overflow-y-auto rounded-t-3xl bg-white p-6">
				{loading ? (
					<div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
						{Array.from({ length: 3 }).map((_, i) => (
							<Skeleton key={i} className="h-48 rounded-lg" />
						))}
					</div>
				) : channels.length === 0 ? (
					<Empty className="border-none py-16">
						<EmptyHeader>
							<EmptyTitle>{t('channel.empty.title')}</EmptyTitle>
							<EmptyDescription>{t('channel.empty.description')}</EmptyDescription>
						</EmptyHeader>
					</Empty>
				) : (
					<div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
						{channels.map((ch) => (
							<ChannelCard
								key={ch.channel_id}
								channel={ch}
								agentName={getAgentName(ch.default_agent_id)}
								onEnable={() => enable(ch.channel_id)}
								onDisable={() => disable(ch.channel_id)}
								onEdit={() => setEditTarget(ch)}
								onDelete={() => setDeleteTarget(ch)}
							/>
						))}
					</div>
				)}
			</div>

			<CreateChannelDialog
				open={createOpen}
				onOpenChange={setCreateOpen}
				onCreated={refetch}
			/>

			<EditChannelDialog
				channel={editTarget}
				open={!!editTarget}
				onOpenChange={(open) => !open && setEditTarget(null)}
				onUpdated={refetch}
			/>

			{deleteTarget && (
				<DeleteDialog
					open={!!deleteTarget}
					onOpenChange={(open) => !open && setDeleteTarget(null)}
					title={t('common.deleteTitle', {
						entity: t('channel.deleteEntity'),
						name: deleteTarget.channel_id,
					})}
					description={t('common.deleteDescription')}
					onConfirm={async () => {
						await remove(deleteTarget.channel_id);
						setDeleteTarget(null);
					}}
				/>
			)}
		</div>
	);
}
