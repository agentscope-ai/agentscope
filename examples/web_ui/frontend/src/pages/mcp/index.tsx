import { Blocks, Check, Download, Pencil, Plug, Trash2, TriangleAlert } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

import { hubApi } from '@/api';
import type { HubInfo, MCPCard, MCPView } from '@/api';
import { InstallMCPDialog } from '@/components/dialog/InstallMCPDialog.tsx';
import { ResourceDetailDrawer } from '@/components/drawer/ResourceDetailDrawer.tsx';
import { LoadMore } from '@/components/hub/LoadMore.tsx';
import { ResourcePanel } from '@/components/hub/ResourcePanel.tsx';
import { Markdown } from '@/components/markdown';
import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar.tsx';
import { Badge } from '@/components/ui/badge.tsx';
import { Button } from '@/components/ui/button.tsx';
import {
	Empty,
	EmptyContent,
	EmptyDescription,
	EmptyHeader,
	EmptyMedia,
	EmptyTitle,
} from '@/components/ui/empty.tsx';
import {
	Item,
	ItemActions,
	ItemContent,
	ItemDescription,
	ItemGroup,
	ItemMedia,
	ItemTitle,
} from '@/components/ui/item.tsx';
import {
	Sidebar,
	SidebarContent,
	SidebarGroup,
	SidebarGroupContent,
	SidebarGroupLabel,
	SidebarHeader,
	SidebarMenu,
	SidebarMenuButton,
	SidebarMenuItem,
} from '@/components/ui/sidebar.tsx';
import { Spinner } from '@/components/ui/spinner.tsx';
import { useMCPHubCards } from '@/hooks/useMCPHubCards.ts';
import { useMCPHubs } from '@/hooks/useMCPHubs.ts';
import { useMCPs } from '@/hooks/useMCPs.ts';
import { useResourceDrawer } from '@/hooks/useResourceDrawer.ts';
import { useTranslation } from '@/i18n/useI18n';
import { cn } from '@/lib/utils';
import { avatarTint, formatTime } from '@/utils/common';

/**
 * The drawer body: the server's README where a skill shows its `SKILL.md`,
 * plus what it will actually be installed as.
 *
 * The `${...}` placeholders are left in — they are resolved server-side
 * from the install form, so this is the template, never the filled-in
 * config with its secrets.
 */
function MCPDetailBody({ card }: { card: MCPCard | null }) {
	const { t } = useTranslation();

	if (!card) return null;

	return (
		<div className="flex flex-col gap-y-6">
			<div className="flex flex-col gap-y-2">
				<span className="text-xs text-muted-foreground">{t('mcp.configLabel')}</span>
				<pre className="overflow-x-auto rounded-md bg-muted p-3 text-xs">
					{JSON.stringify(card.config_template, null, 2)}
				</pre>
			</div>
			{card.readme && <Markdown>{card.readme}</Markdown>}
		</div>
	);
}

interface CardItemProps {
	card: MCPCard;
	installed: boolean;
	/** "Now" in epoch seconds, pinned by the panel so every row in a
	 *  render agrees and the age does not shift on unrelated re-renders. */
	now: number;
	onInstall: () => void;
	onOpen: () => void;
}

function CardItem({ card, installed, now, onInstall, onOpen }: CardItemProps) {
	const { t } = useTranslation();

	return (
		<Item
			className={cn(
				'cursor-pointer items-start rounded-none border-l-2 py-[13px] pr-[18px] pl-4 hover:bg-row-hover',
				// The left rule is the "installed" marker. Only the icon
				// dims with it — fading the whole row would drop the
				// description to a 2.4:1 contrast ratio.
				installed ? 'border-l-foreground' : 'border-l-transparent',
			)}
			onClick={onOpen}
		>
			<ItemMedia className={cn('translate-y-0', installed && 'opacity-55')}>
				<Avatar className="rounded-md">
					<AvatarImage
						src={card.icon_url ?? undefined}
						alt={card.display_name || card.name}
						loading="lazy"
					/>
					<AvatarFallback className="rounded-md" style={avatarTint(card.name)}>
						{(card.display_name || card.name).slice(0, 1).toUpperCase()}
					</AvatarFallback>
				</Avatar>
			</ItemMedia>

			<ItemContent>
				{/* Name, author, tags — each step lighter than the last, so
				    the eye lands on the name first. */}
				<ItemTitle>
					<span className="font-medium">{card.display_name || card.name}</span>
					{card.author && (
						<span className="text-xs text-muted-foreground">@{card.author}</span>
					)}
					{card.auth === 'inputs' && (
						// Sans, not mono: this one is a warning meant for a
						// person, while the tags below are machine metadata.
						<Badge className="rounded-full border-0 bg-amber-600/10 px-2 py-0.5 text-[10.5px] font-normal text-amber-700">
							{t('mcp.needsConfig')}
						</Badge>
					)}
					{card.tags.slice(0, 4).map((tag) => (
						<Badge
							key={tag}
							variant="secondary"
							className="rounded-full bg-secondary px-1.75 py-0.5 font-mono text-[10px] font-normal text-text-tertiary"
						>
							{tag}
						</Badge>
					))}
				</ItemTitle>
				<ItemDescription className="line-clamp-1">{card.description}</ItemDescription>
			</ItemContent>

			<ItemActions className="items-center gap-3 self-center">
				<span className="font-mono text-[10px] text-text-data whitespace-nowrap">
					{card.updated_at
						? now - card.updated_at < 3600
							? t('skill.updatedRecently')
							: t('skill.updatedAgo', {
									ago: formatTime(now - card.updated_at, {
										leadingUnitOnly: true,
									}),
								})
						: null}
				</span>
				<div className="flex h-8 items-center gap-2">
					{/* Explicit null check: a hub reporting 0 installs is
					    saying something, one that does not count them is not. */}
					{card.installs != null && (
						<span className="inline-flex items-center gap-1 font-mono text-[10px] text-text-data">
							<Download className="size-3" />
							{card.installs.toLocaleString()}
						</span>
					)}
					{installed ? (
						// A state, not an action — a disabled button would
						// still read as something you could have clicked.
						<span className="flex h-7 items-center gap-x-[5px] rounded-full bg-surface-muted px-3 text-[11.5px] text-muted-foreground">
							<Check className="size-3" />
							{t('mcp.installed')}
						</span>
					) : (
						<Button
							className="h-7 rounded-full px-3.5 text-xs font-normal hover:opacity-86"
							// Installing straight from the row must not also
							// open the drawer behind it.
							onClick={(e) => {
								e.stopPropagation();
								onInstall();
							}}
						>
							{t('mcp.install')}
						</Button>
					)}
				</div>
			</ItemActions>
		</Item>
	);
}

interface HubPanelProps {
	hubId: string;
	hub?: HubInfo;
	installedNames: Set<string>;
	onInstalled: () => void;
}

function HubPanel({ hubId, hub, installedNames, onInstalled }: HubPanelProps) {
	const { t } = useTranslation();
	const [query, setQuery] = useState('');
	const [installing, setInstalling] = useState<MCPCard | null>(null);
	// Read once per mount: reading the clock during render is impure, and
	// a browse session is far shorter than the units this rounds to.
	const [now] = useState(() => Date.now() / 1000);
	// The list already carries everything the drawer shows, but the detail
	// endpoint is where a hub may fill in more.
	const drawer = useResourceDrawer(
		useCallback((card) => hubApi.mcp.getCard(card.hub_id as string, (card as MCPCard).id), []),
	);
	const { cards, loading, loadingMore, error, hasMore, loadMore, refetch } = useMCPHubCards(
		hubId,
		query,
	);

	return (
		// Falls back to the raw id while the hub list is still loading.
		<ResourcePanel
			title={hub?.display_name ?? hubId}
			description={hub?.description}
			icon={
				<Avatar className="rounded-md">
					<AvatarImage
						src={hub?.icon_url ?? undefined}
						alt={hub?.display_name ?? hubId}
					/>
					<AvatarFallback className="rounded-md" style={avatarTint(hubId)}>
						{(hub?.display_name ?? hubId).slice(0, 1).toUpperCase()}
					</AvatarFallback>
				</Avatar>
			}
			search={{
				value: query,
				onChange: setQuery,
				placeholder: t('mcp.searchPlaceholder'),
			}}
		>
			<div className="flex flex-col gap-y-4">
				{loading ? (
					<div className="flex justify-center py-10">
						<Spinner />
					</div>
				) : error ? (
					<Empty className="border-none py-10">
						<EmptyHeader>
							<EmptyMedia variant="icon">
								<TriangleAlert />
							</EmptyMedia>
							<EmptyTitle>{t('mcp.loadFailedTitle')}</EmptyTitle>
							<EmptyDescription>{t('mcp.loadFailedDescription')}</EmptyDescription>
						</EmptyHeader>
						<EmptyContent>
							<Button variant="outline" size="sm" onClick={refetch}>
								{t('mcp.retry')}
							</Button>
						</EmptyContent>
					</Empty>
				) : cards.length === 0 ? (
					<Empty className="border-none py-10">
						<EmptyHeader>
							<EmptyMedia variant="icon">
								<Blocks />
							</EmptyMedia>
							<EmptyTitle>{t('mcp.noCardsTitle')}</EmptyTitle>
							<EmptyDescription>{t('mcp.noCardsDescription')}</EmptyDescription>
						</EmptyHeader>
					</Empty>
				) : (
					<ItemGroup className="gap-0">
						{cards.map((card) => (
							<CardItem
								key={`${card.hub_id}:${card.id}`}
								card={card}
								installed={installedNames.has(card.name)}
								now={now}
								onInstall={() => setInstalling(card)}
								onOpen={() => drawer.open(card)}
							/>
						))}
					</ItemGroup>
				)}

				{/* Cursor pagination — no page numbers and no total to show. */}
				{hasMore && (
					<LoadMore shown={cards.length} loading={loadingMore} onLoad={loadMore} />
				)}
			</div>

			<ResourceDetailDrawer
				skill={drawer.opened}
				loading={drawer.loading}
				onOpenChange={(open) => {
					if (!open) drawer.close();
				}}
				body={<MCPDetailBody card={drawer.opened as MCPCard | null} />}
				action={
					<Button
						disabled={drawer.opened !== null && installedNames.has(drawer.opened.name)}
						onClick={() => {
							// One overlay at a time: the install form replaces
							// the drawer rather than stacking on top of it.
							setInstalling(drawer.opened as MCPCard);
							drawer.close();
						}}
					>
						{drawer.opened && installedNames.has(drawer.opened.name) ? (
							<Check />
						) : (
							<Download />
						)}
						{t(
							drawer.opened && installedNames.has(drawer.opened.name)
								? 'mcp.installed'
								: 'mcp.install',
						)}
					</Button>
				}
			/>

			<InstallMCPDialog
				card={installing}
				onOpenChange={(open) => {
					if (!open) setInstalling(null);
				}}
				onInstalled={onInstalled}
			/>
		</ResourcePanel>
	);
}

interface MinePanelProps {
	mcps: MCPView[];
	loading: boolean;
	onEdit: (mcp: MCPView) => void;
	onRemove: (mcpId: string) => void;
}

function MinePanel({ mcps, loading, onEdit, onRemove }: MinePanelProps) {
	const { t } = useTranslation();
	const [query, setQuery] = useState('');

	// Filtered client-side: the library is the user's own and small, so a
	// round trip per keystroke would buy nothing.
	const needle = query.trim().toLowerCase();
	const shown = needle
		? mcps.filter((mcp) =>
				[mcp.name, mcp.display_name ?? '', mcp.description, ...mcp.tags].some((field) =>
					field.toLowerCase().includes(needle),
				),
			)
		: mcps;

	return (
		<ResourcePanel
			title={t('common.my-mcp')}
			description={t('mcp.mineDescription')}
			icon={<Plug className="size-5 text-muted-foreground" />}
			search={
				// Hidden while there is nothing to search through.
				mcps.length > 0
					? {
							value: query,
							onChange: setQuery,
							placeholder: t('mcp.mineSearchPlaceholder'),
						}
					: undefined
			}
		>
			{loading ? (
				<div className="flex justify-center py-10">
					<Spinner />
				</div>
			) : mcps.length === 0 ? (
				<Empty className="border-none py-10">
					<EmptyHeader>
						<EmptyMedia variant="icon">
							<Plug />
						</EmptyMedia>
						<EmptyTitle>{t('mcp.mineEmptyTitle')}</EmptyTitle>
						<EmptyDescription>{t('mcp.mineEmptyDescription')}</EmptyDescription>
					</EmptyHeader>
				</Empty>
			) : shown.length === 0 ? (
				<Empty className="border-none py-10">
					<EmptyHeader>
						<EmptyMedia variant="icon">
							<Blocks />
						</EmptyMedia>
						<EmptyTitle>{t('mcp.noCardsTitle')}</EmptyTitle>
						<EmptyDescription>{t('mcp.noCardsDescription')}</EmptyDescription>
					</EmptyHeader>
				</Empty>
			) : (
				<ItemGroup className="gap-0">
					{shown.map((mcp) => (
						<Item key={mcp.id}>
							<ItemMedia>
								<Avatar className="rounded-md">
									<AvatarImage
										src={mcp.icon_url ?? undefined}
										alt={mcp.display_name || mcp.name}
										loading="lazy"
									/>
									<AvatarFallback
										className="rounded-md"
										style={avatarTint(mcp.name)}
									>
										{(mcp.display_name || mcp.name).slice(0, 1).toUpperCase()}
									</AvatarFallback>
								</Avatar>
							</ItemMedia>

							<ItemContent>
								<ItemTitle>
									<span className="font-medium">
										{mcp.display_name || mcp.name}
									</span>
									{mcp.author && (
										<span className="text-xs text-muted-foreground">
											@{mcp.author}
										</span>
									)}
									{mcp.is_stateful && (
										<span
											key={'state'}
											className="text-xs text-muted-foreground/60"
										>
											#{t('mcp.stateful')}
										</span>
									)}
									{mcp.tags.slice(0, 4).map((tag) => (
										<span
											key={tag}
											className="text-xs text-muted-foreground/60"
										>
											#{tag}
										</span>
									))}
								</ItemTitle>
								<ItemDescription className="line-clamp-1">
									{mcp.description}
								</ItemDescription>
							</ItemContent>

							<ItemActions className="gap-1">
								{mcp.version && (
									<span className="text-xs text-muted-foreground whitespace-nowrap">
										{mcp.version}
									</span>
								)}
								{mcp.hub_id && mcp.card_id && (
									<Button
										size="icon-sm"
										variant="ghost"
										className="text-muted-foreground"
										onClick={() => onEdit(mcp)}
										title={t('common.edit')}
									>
										<Pencil />
									</Button>
								)}
								<Button
									size="icon-sm"
									variant="ghost"
									className="text-muted-foreground"
									onClick={() => onRemove(mcp.id)}
									title={t('common.delete')}
								>
									<Trash2 />
								</Button>
							</ItemActions>
						</Item>
					))}
				</ItemGroup>
			)}
		</ResourcePanel>
	);
}

export function MCPHubPage() {
	const { t } = useTranslation();
	const navigate = useNavigate();
	// No `hubId` in the URL means the "mine" tab, which is the default.
	const { hubId } = useParams<{ hubId?: string }>();
	const { hubs, loading: hubsLoading, error: hubsError, refetch } = useMCPHubs();
	// Loaded page-wide, not per panel: the hub view needs it to mark cards
	// as already installed, and the "mine" view to list them.
	const { mcps, loading: mcpsLoading, refetch: refetchMcps, remove } = useMCPs();
	const installedNames = new Set(mcps.map((mcp) => mcp.name));
	// Editing re-opens the install form against the originating card, so
	// the card has to be fetched before the dialog can render its inputs.
	const [editing, setEditing] = useState<MCPView | null>(null);
	const [editingCard, setEditingCard] = useState<MCPCard | null>(null);

	useEffect(() => {
		if (!editing?.hub_id || !editing.card_id) return;
		let current = true;
		hubApi.mcp
			.getCard(editing.hub_id, editing.card_id)
			.then((card) => {
				if (current) setEditingCard(card);
			})
			.catch(() => {
				// The hub is gone or the card was dropped; the dialog stays
				// shut and the toast from the client says why.
				if (current) setEditing(null);
			});
		return () => {
			current = false;
		};
	}, [editing]);

	return (
		<div className="flex size-full p-2 gap-2">
			<Sidebar collapsible="none" className="rounded-[22px]">
				<SidebarHeader className="flex flex-col p-[20px_18px_14px] gap-y-1">
					<div className="text-xl font-medium tracking-[-0.02em] text-foreground">
						{t('common.mcp-hub')}
					</div>
					<div className="text-text-tertiary text-xs">{t('mcp.subtitle')}</div>
				</SidebarHeader>
				<SidebarContent>
					<SidebarGroup className="mt-6 px-2 py-0">
						<SidebarGroupLabel>{t('common.mine')}</SidebarGroupLabel>
						<SidebarGroupContent>
							<SidebarMenu>
								<SidebarMenuItem>
									<SidebarMenuButton
										isActive={!hubId}
										onClick={() => navigate('/mcp')}
									>
										<Plug />
										<span className="min-w-0 flex-1 truncate">
											{t('common.my-mcp')}
										</span>
										<span className="font-mono text-[10px] text-text-data">
											{mcps.length}
										</span>
									</SidebarMenuButton>
								</SidebarMenuItem>
							</SidebarMenu>
						</SidebarGroupContent>
					</SidebarGroup>

					<SidebarGroup className="mt-5 px-2 py-0">
						<SidebarGroupLabel className="justify-between">
							{t('mcp.hubsLabel')}
							{hubs.length > 0 && (
								<span className="text-[10px] text-text-data font-mono">
									{hubs.length}
								</span>
							)}
						</SidebarGroupLabel>
						<SidebarGroupContent>
							{hubsLoading ? (
								<div className="flex justify-center py-4">
									<Spinner />
								</div>
							) : hubsError ? (
								// Distinct from the empty state on purpose: a
								// failed request otherwise reads as "no hubs
								// configured", pointing at the wrong problem.
								<Empty className="border-none py-4 min-h-40">
									<EmptyHeader>
										<EmptyMedia variant="icon">
											<TriangleAlert />
										</EmptyMedia>
										<EmptyTitle>{t('mcp.loadFailedTitle')}</EmptyTitle>
										<EmptyDescription>
											{t('mcp.loadFailedDescription')}
										</EmptyDescription>
									</EmptyHeader>
									<EmptyContent>
										<Button variant="outline" size="sm" onClick={refetch}>
											{t('mcp.retry')}
										</Button>
									</EmptyContent>
								</Empty>
							) : hubs.length === 0 ? (
								<Empty className="border-none py-4 min-h-40">
									<EmptyHeader>
										<EmptyMedia variant="icon">
											<Blocks />
										</EmptyMedia>
										<EmptyTitle>{t('mcp.noHubsTitle')}</EmptyTitle>
										<EmptyDescription>
											{t('mcp.noHubsDescription')}
										</EmptyDescription>
									</EmptyHeader>
								</Empty>
							) : (
								<SidebarMenu>
									{hubs.map((hub) => (
										<SidebarMenuItem key={hub.hub_id}>
											<SidebarMenuButton
												isActive={hubId === hub.hub_id}
												onClick={() => navigate(`/mcp/${hub.hub_id}`)}
												title={hub.description}
											>
												<Avatar className="size-4 rounded-sm">
													<AvatarImage
														src={hub.icon_url ?? undefined}
														alt={hub.display_name}
													/>
													<AvatarFallback
														className="rounded-sm text-[10px]"
														style={avatarTint(hub.hub_id)}
													>
														{hub.display_name.slice(0, 1).toUpperCase()}
													</AvatarFallback>
												</Avatar>
												<span className="min-w-0 flex-1 truncate">
													{hub.display_name}
												</span>
											</SidebarMenuButton>
										</SidebarMenuItem>
									))}
								</SidebarMenu>
							)}
						</SidebarGroupContent>
					</SidebarGroup>
				</SidebarContent>
			</Sidebar>

			<main className="flex-1 min-w-0 min-h-0 overflow-hidden rounded-[22px] bg-card shadow-panel">
				{hubId ? (
					// Remount on hub change so the panel's query box resets.
					<HubPanel
						key={hubId}
						hubId={hubId}
						hub={hubs.find((h) => h.hub_id === hubId)}
						installedNames={installedNames}
						onInstalled={refetchMcps}
					/>
				) : (
					<MinePanel
						mcps={mcps}
						loading={mcpsLoading}
						onEdit={setEditing}
						onRemove={remove}
					/>
				)}
			</main>

			<InstallMCPDialog
				card={editing ? editingCard : null}
				editing={editing}
				onOpenChange={(open) => {
					if (!open) {
						setEditing(null);
						setEditingCard(null);
					}
				}}
				onInstalled={refetchMcps}
			/>
		</div>
	);
}
