import { Cable, Plus } from 'lucide-react';
import * as React from 'react';

import type { ChannelRecord, ChannelStatus, ChannelTypeSchema } from '@/api';
import { channelApi } from '@/api';
import { DeleteDialog } from '@/components/dialog/DeleteDialog';
import {
	Empty,
	EmptyDescription,
	EmptyHeader,
	EmptyMedia,
	EmptyTitle,
} from '@/components/ui/empty';
import {
	Item,
	ItemActions,
	ItemContent,
	ItemHeader,
	ItemMedia,
	ItemTitle,
} from '@/components/ui/item';
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from '@/components/ui/resizable';
import { Separator } from '@/components/ui/separator';
import { Skeleton } from '@/components/ui/skeleton';
import { Switch } from '@/components/ui/switch';
import { useAgents } from '@/hooks/useAgents';
import { useChannels } from '@/hooks/useChannels';
import { useTranslation } from '@/i18n/useI18n';
import { ChannelDetailPanel } from '@/pages/channel/channel-detail-panel';
import { ChannelStatusBadge } from '@/pages/channel/channel-status';
import { CreateChannelDialog } from '@/pages/channel/create-channel-dialog';
import { EditChannelDialog } from '@/pages/channel/edit-channel-dialog';
import { TypeAvatar } from '@/pages/channel/type-avatar';

function ChannelCard({
	channel,
	type,
	agentName,
	status,
	onEnable,
	onDisable,
	onOpen,
}: {
	channel: ChannelRecord;
	type?: ChannelTypeSchema;
	agentName: string;
	status?: ChannelStatus | null;
	onEnable: () => void;
	onDisable: () => void;
	onOpen: () => void;
}) {
	const { t } = useTranslation();
	const typeName = type?.display_name ?? channel.channel_type;
	const name = channel.name?.trim() || typeName;
	const model = channel.session.chat_model_config?.model;

	return (
		<Item
			variant="outline"
			onClick={onOpen}
			className="cursor-pointer items-start shadow-panel transition hover:border-ring/40"
		>
			<ItemHeader>
				<div className="flex min-w-0 items-center gap-3">
					<ItemMedia>
						<TypeAvatar type={type} className="size-9 rounded-lg" />
					</ItemMedia>
					<div className="min-w-0">
						<ItemTitle className="truncate">{name}</ItemTitle>
						<span className="font-mono text-[11px] text-muted-foreground">
							{typeName}
						</span>
					</div>
				</div>
				<ItemActions>
					<Switch
						checked={channel.enabled}
						onClick={(e) => e.stopPropagation()}
						onCheckedChange={(v) => (v ? onEnable() : onDisable())}
					/>
				</ItemActions>
			</ItemHeader>
			<ItemContent className="basis-full gap-1.5 text-xs">
				<div className="flex items-center justify-between gap-2">
					<span className="text-muted-foreground">{t('common.agent')}</span>
					<span className="truncate">{agentName}</span>
				</div>
				<div className="flex items-center justify-between gap-2">
					<span className="text-muted-foreground">{t('channel.routing')}</span>
					<span className="font-mono">
						{t('channel.rules', { count: channel.routing.bindings.length })}
					</span>
				</div>
				{model && (
					<div className="flex items-center justify-between gap-2">
						<span className="text-muted-foreground">{t('common.model')}</span>
						<span className="truncate font-mono">{model}</span>
					</div>
				)}
				<div className="flex items-center justify-between gap-2">
					<span className="text-muted-foreground">{t('channel.status')}</span>
					<ChannelStatusBadge enabled={channel.enabled} status={status} />
				</div>
			</ItemContent>
		</Item>
	);
}

function ChannelTypeCard({ type, onPick }: { type: ChannelTypeSchema; onPick: () => void }) {
	return (
		<button
			onClick={onPick}
			className="group flex items-start gap-3 rounded-xl border bg-card p-4 text-left shadow-panel transition hover:border-ring/40"
		>
			<TypeAvatar type={type} className="size-10 rounded-lg" />
			<div className="min-w-0 flex-1">
				<div className="truncate text-sm font-semibold">{type.display_name}</div>
				{type.description ? (
					<div className="mt-0.5 line-clamp-2 text-xs text-muted-foreground">
						{type.description}
					</div>
				) : (
					<div className="font-mono text-[11px] text-muted-foreground">
						{type.channel_type}
					</div>
				)}
			</div>
			<Plus className="size-4 shrink-0 text-muted-foreground opacity-0 transition group-hover:opacity-100" />
		</button>
	);
}

export function ChannelPage() {
	const { t } = useTranslation();
	const { channels, loading, refetch, enable, disable, remove } = useChannels();
	const { agents } = useAgents();
	const [types, setTypes] = React.useState<ChannelTypeSchema[]>([]);
	const [createType, setCreateType] = React.useState<string | null>(null);
	const [editTarget, setEditTarget] = React.useState<ChannelRecord | null>(null);
	const [deleteTarget, setDeleteTarget] = React.useState<ChannelRecord | null>(null);
	const [selectedId, setSelectedId] = React.useState<string | null>(null);
	const [statuses, setStatuses] = React.useState<Record<string, ChannelStatus>>({});

	React.useEffect(() => {
		channelApi
			.listTypes()
			.then(setTypes)
			.catch(() => {});
	}, []);

	// One status source, polled and shared by the cards and the detail panel
	// so they never disagree. Only enabled channels run, so only they have a
	// status; disabled ones show "已禁用" from the enabled flag.
	const channelIds = channels
		.filter((c) => c.enabled)
		.map((c) => c.id)
		.join(',');
	React.useEffect(() => {
		const ids = channelIds ? channelIds.split(',') : [];
		if (ids.length === 0) {
			setStatuses({});
			return;
		}
		let alive = true;
		const load = () => {
			Promise.all(
				ids.map((id) =>
					channelApi
						.status(id)
						.then((s) => [id, s] as const)
						.catch(() => null),
				),
			).then((rs) => {
				if (!alive) return;
				const next: Record<string, ChannelStatus> = {};
				for (const r of rs) if (r) next[r[0]] = r[1];
				setStatuses(next);
			});
		};
		load();
		const iv = setInterval(load, 10000);
		return () => {
			alive = false;
			clearInterval(iv);
		};
	}, [channelIds]);

	const typeOf = (channelType: string) => types.find((ct) => ct.channel_type === channelType);
	const agentName = (agentId: string) =>
		agents.find((a) => a.id === agentId)?.data.name ?? agentId.slice(0, 8);
	const deleteName = (ch: ChannelRecord) =>
		ch.name?.trim() ||
		`${typeOf(ch.channel_type)?.display_name ?? ch.channel_type} · ${ch.id.slice(0, 8)}`;
	// Derived from the live list so an edited/deleted selection stays in sync.
	const selected = channels.find((c) => c.id === selectedId) ?? null;

	return (
		<div className="flex size-full p-2">
			<ResizablePanelGroup orientation="horizontal">
				<ResizablePanel minSize="24rem">
					<main className="flex h-full min-h-0 min-w-0 flex-col overflow-hidden rounded-[22px] bg-card shadow-panel">
						<div className="px-6 pt-5 pb-4">
							<div className="text-2xl font-semibold">{t('channel.title')}</div>
							<div className="mt-1 text-sm text-muted-foreground">
								{t('channel.subtitle')}
							</div>
						</div>
						<Separator />

						<div className="flex-1 overflow-y-auto px-6 py-6">
							{loading ? (
								<div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
									{Array.from({ length: 4 }).map((_, i) => (
										<Skeleton key={i} className="h-40 rounded-lg" />
									))}
								</div>
							) : (
								<>
									{channels.length === 0 ? (
										<Empty className="border-none py-10">
											<EmptyHeader>
												<EmptyMedia variant="icon">
													<Cable />
												</EmptyMedia>
												<EmptyTitle>{t('channel.empty.title')}</EmptyTitle>
												<EmptyDescription>
													{t('channel.empty.description')}
												</EmptyDescription>
											</EmptyHeader>
										</Empty>
									) : (
										<section className="mb-2">
											<div className="mb-4 flex items-center gap-2">
												<span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
													{t('channel.sectionActive')}
												</span>
												<span className="rounded bg-muted px-2 py-0.5 font-mono text-[10px] text-muted-foreground">
													{channels.length}
												</span>
											</div>
											<div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
												{channels.map((ch) => (
													<ChannelCard
														key={ch.id}
														channel={ch}
														type={typeOf(ch.channel_type)}
														agentName={agentName(
															ch.routing.bindings[
																ch.routing.bindings.length - 1
															]?.agent_id ?? '',
														)}
														status={statuses[ch.id]}
														onEnable={() => enable(ch.id)}
														onDisable={() => disable(ch.id)}
														onOpen={() => setSelectedId(ch.id)}
													/>
												))}
											</div>
										</section>
									)}

									<div className="my-8 flex items-center gap-4">
										<span className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
											<Plus className="size-3.5 text-primary" />
											{t('channel.sectionAdd')}
										</span>
										<div className="flex-1 border-t border-dashed" />
									</div>

									<div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4 gap-3">
										{types.map((ct) => (
											<ChannelTypeCard
												key={ct.channel_type}
												type={ct}
												onPick={() => setCreateType(ct.channel_type)}
											/>
										))}
									</div>
								</>
							)}
						</div>
					</main>
				</ResizablePanel>
				{selected && (
					<>
						<ResizableHandle withHandle className="w-2 bg-transparent" />
						<ResizablePanel defaultSize="26rem" minSize="22rem">
							<ChannelDetailPanel
								channel={selected}
								type={typeOf(selected.channel_type)}
								status={statuses[selected.id]}
								agentName={agentName}
								onEdit={() => setEditTarget(selected)}
								onDelete={() => setDeleteTarget(selected)}
								onClose={() => setSelectedId(null)}
							/>
						</ResizablePanel>
					</>
				)}
			</ResizablePanelGroup>

			<CreateChannelDialog
				open={createType !== null}
				initialType={createType ?? undefined}
				onOpenChange={(open) => !open && setCreateType(null)}
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
						name: deleteName(deleteTarget),
					})}
					description={t('common.deleteDescription')}
					onConfirm={async () => {
						await remove(deleteTarget.id);
						setDeleteTarget(null);
					}}
				/>
			)}
		</div>
	);
}
