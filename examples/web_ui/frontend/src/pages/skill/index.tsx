import { Blocks, Check, Download, Plug, Trash2, TriangleAlert } from 'lucide-react';
import { useCallback, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

import type { HubInfo, SkillCard, SkillView } from '@/api';
import { hubApi, skillApi } from '@/api';
import { ApiError } from '@/api/client';
import { ResourceDetailDrawer } from '@/components/drawer/ResourceDetailDrawer.tsx';
import { LoadMore } from '@/components/hub/LoadMore.tsx';
import { ResourcePanel } from '@/components/hub/ResourcePanel.tsx';
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
import { useResourceDrawer } from '@/hooks/useResourceDrawer.ts';
import { useSkillHubCards } from '@/hooks/useSkillHubCards.ts';
import { useSkillHubs } from '@/hooks/useSkillHubs.ts';
import { useSkills } from '@/hooks/useSkills.ts';
import { useTranslation } from '@/i18n/useI18n';
import { cn } from '@/lib/utils';
import { avatarTint, formatTime } from '@/utils/common';

interface CardItemProps {
	card: SkillCard;
	installed: boolean;
	installing: boolean;
	/** "Now" in epoch seconds, pinned by the panel so every row in a
	 *  render agrees and the age does not shift on unrelated re-renders. */
	now: number;
	onInstall: () => void;
	onOpen: () => void;
}

function CardItem({ card, installed, installing, now, onInstall, onOpen }: CardItemProps) {
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
					{card.tags.slice(0, 4).map((tag) => (
						<Badge
							key={tag}
							variant="secondary"
							className="rounded-full bg-secondary px-1.75 py-0.5 font-mono text-[10px] font-normal text-text-tertiary"
						>
							{tag}
						</Badge>
					))}
					{/* Explicit null check: a hub reporting 0 downloads is
					    saying something, one that does not count them is not. */}
					{card.downloads != null && (
						<Badge
							variant="secondary"
							className="gap-x-1 rounded-full bg-secondary px-1.75 py-0.5 font-mono text-[10px] font-normal text-text-tertiary"
						>
							<Download className="size-2.5" />
							{card.downloads.toLocaleString()}
						</Badge>
					)}
				</ItemTitle>
				<ItemDescription className="line-clamp-1">{card.description}</ItemDescription>
			</ItemContent>

			<ItemActions className="items-center gap-3 self-center">
				<span className="font-mono text-[10px] text-text-data whitespace-nowrap">
					{/* updated_at is in seconds, as is formatTime. */}
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
					{installed ? (
						// A state, not an action — a disabled button would
						// still read as something you could have clicked.
						<span className="flex h-7 items-center gap-x-[5px] rounded-full bg-surface-muted px-3 text-[11.5px] text-muted-foreground">
							<Check className="size-3" />
							{t('skill.installed')}
						</span>
					) : (
						<Button
							className="h-7 rounded-full px-3.5 text-xs font-normal hover:opacity-86"
							disabled={installing}
							// Installing straight from the row must not also
							// open the drawer behind it.
							onClick={(e) => {
								e.stopPropagation();
								onInstall();
							}}
						>
							{installing && <Spinner />}
							{t('skill.install')}
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
	const [installingId, setInstallingId] = useState<string | null>(null);
	const drawer = useResourceDrawer(
		useCallback(
			(skill) => hubApi.skill.getCard(skill.hub_id as string, (skill as SkillCard).id),
			[],
		),
	);
	// Read once per mount: reading the clock during render is impure, and
	// a browse session is far shorter than the units this rounds to.
	const [now] = useState(() => Date.now() / 1000);
	const { cards, loading, loadingMore, error, hasMore, loadMore, refetch } = useSkillHubCards(
		hubId,
		query,
	);

	// A skill needs no configuration, so there is nothing to ask for —
	// unlike an MCP install, this goes straight through without a dialog.
	const handleInstall = async (card: SkillCard) => {
		setInstallingId(card.id);
		try {
			await hubApi.skill.install(card.hub_id, card.id);
			onInstalled();
		} catch (e) {
			// A 409 already surfaced as a toast from the client.
			if (!(e instanceof ApiError)) throw e;
		} finally {
			setInstallingId(null);
		}
	};

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
				placeholder: t('skill.searchPlaceholder'),
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
							<EmptyTitle>{t('skill.loadFailedTitle')}</EmptyTitle>
							<EmptyDescription>{t('skill.loadFailedDescription')}</EmptyDescription>
						</EmptyHeader>
						<EmptyContent>
							<Button variant="outline" size="sm" onClick={refetch}>
								{t('skill.retry')}
							</Button>
						</EmptyContent>
					</Empty>
				) : cards.length === 0 ? (
					<Empty className="border-none py-10">
						<EmptyHeader>
							<EmptyMedia variant="icon">
								<Blocks />
							</EmptyMedia>
							<EmptyTitle>{t('skill.noCardsTitle')}</EmptyTitle>
							<EmptyDescription>{t('skill.noCardsDescription')}</EmptyDescription>
						</EmptyHeader>
					</Empty>
				) : (
					<ItemGroup className="gap-0">
						{cards.map((card) => (
							<CardItem
								key={`${card.hub_id}:${card.id}`}
								card={card}
								installed={installedNames.has(card.name)}
								installing={installingId === card.id}
								now={now}
								onInstall={() => handleInstall(card)}
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
				action={
					<Button
						disabled={
							drawer.opened !== null &&
							(installedNames.has(drawer.opened.name) ||
								installingId === (drawer.opened as SkillCard).id)
						}
						onClick={() => drawer.opened && handleInstall(drawer.opened as SkillCard)}
					>
						{drawer.opened && installedNames.has(drawer.opened.name) ? (
							<Check />
						) : (
							<Download />
						)}
						{t(
							drawer.opened && installedNames.has(drawer.opened.name)
								? 'skill.installed'
								: 'skill.install',
						)}
					</Button>
				}
			/>
		</ResourcePanel>
	);
}

interface MinePanelProps {
	skills: SkillView[];
	loading: boolean;
	onRemove: (skillId: string) => void;
}

function MinePanel({ skills, loading, onRemove }: MinePanelProps) {
	const { t } = useTranslation();
	const [query, setQuery] = useState('');
	// The list view omits SKILL.md; the detail endpoint carries it.
	const drawer = useResourceDrawer(
		useCallback((skill) => skillApi.get((skill as SkillView).id), []),
	);

	// Filtered client-side: the library is the user's own and small, so a
	// round trip per keystroke would buy nothing.
	const needle = query.trim().toLowerCase();
	const shown = needle
		? skills.filter((skill) =>
				[skill.name, skill.display_name ?? '', skill.description, ...skill.tags].some(
					(field) => field.toLowerCase().includes(needle),
				),
			)
		: skills;

	return (
		<ResourcePanel
			title={t('common.my-skill')}
			description={t('skill.mineDescription')}
			icon={<Plug className="size-5 text-muted-foreground" />}
			search={
				// Hidden while there is nothing to search through.
				skills.length > 0
					? {
							value: query,
							onChange: setQuery,
							placeholder: t('skill.mineSearchPlaceholder'),
						}
					: undefined
			}
		>
			{loading ? (
				<div className="flex justify-center py-10">
					<Spinner />
				</div>
			) : skills.length === 0 ? (
				<Empty className="border-none py-10">
					<EmptyHeader>
						<EmptyMedia variant="icon">
							<Plug />
						</EmptyMedia>
						<EmptyTitle>{t('skill.mineEmptyTitle')}</EmptyTitle>
						<EmptyDescription>{t('skill.mineEmptyDescription')}</EmptyDescription>
					</EmptyHeader>
				</Empty>
			) : shown.length === 0 ? (
				<Empty className="border-none py-10">
					<EmptyHeader>
						<EmptyMedia variant="icon">
							<Blocks />
						</EmptyMedia>
						<EmptyTitle>{t('skill.noCardsTitle')}</EmptyTitle>
						<EmptyDescription>{t('skill.noCardsDescription')}</EmptyDescription>
					</EmptyHeader>
				</Empty>
			) : (
				<ItemGroup className="gap-0">
					{shown.map((skill) => (
						<Item
							key={skill.id}
							className="cursor-pointer hover:bg-accent/50"
							onClick={() => drawer.open(skill)}
						>
							<ItemMedia>
								<Avatar className="rounded-md">
									<AvatarImage
										src={skill.icon_url ?? undefined}
										alt={skill.display_name || skill.name}
										loading="lazy"
									/>
									<AvatarFallback
										className="rounded-md"
										style={avatarTint(skill.name)}
									>
										{(skill.display_name || skill.name)
											.slice(0, 1)
											.toUpperCase()}
									</AvatarFallback>
								</Avatar>
							</ItemMedia>

							<ItemContent>
								<ItemTitle>
									<span className="font-medium">
										{skill.display_name || skill.name}
									</span>
									{/* A hand-added skill has no hub to name. */}
									{skill.hub_id && (
										<span className="text-xs text-muted-foreground">
											@{skill.hub_id}
										</span>
									)}
									{skill.tags.slice(0, 4).map((tag) => (
										<span
											key={tag}
											className="text-xs text-muted-foreground/60"
										>
											#{tag}
										</span>
									))}
								</ItemTitle>
								<ItemDescription className="line-clamp-1">
									{skill.description}
								</ItemDescription>
							</ItemContent>

							<ItemActions>
								{skill.version && (
									<span className="text-xs text-muted-foreground whitespace-nowrap">
										{skill.version}
									</span>
								)}
								<Button
									size="icon-sm"
									variant="ghost"
									// Deleting from the row must not also
									// open the drawer behind it.
									onClick={(e) => {
										e.stopPropagation();
										onRemove(skill.id);
									}}
									title={t('common.delete')}
								>
									<Trash2 />
								</Button>
							</ItemActions>
						</Item>
					))}
				</ItemGroup>
			)}

			<ResourceDetailDrawer
				skill={drawer.opened}
				loading={drawer.loading}
				onOpenChange={(open) => {
					if (!open) drawer.close();
				}}
				action={
					<Button
						variant="destructive"
						onClick={() => {
							if (drawer.opened) onRemove((drawer.opened as SkillView).id);
							drawer.close();
						}}
					>
						<Trash2 />
						{t('common.delete')}
					</Button>
				}
			/>
		</ResourcePanel>
	);
}

export function SkillHubPage() {
	const { t } = useTranslation();
	const navigate = useNavigate();
	// No `hubId` in the URL means the "mine" tab, which is the default.
	const { hubId } = useParams<{ hubId?: string }>();
	const { hubs, loading: hubsLoading, error: hubsError, refetch } = useSkillHubs();
	// Loaded page-wide, not per panel: the hub view needs it to mark cards
	// as already installed, and the "mine" view to list them.
	const { skills, loading: skillsLoading, refetch: refetchSkills, remove } = useSkills();
	const installedNames = new Set(skills.map((skill) => skill.name));

	return (
		<div className="flex size-full p-2 gap-2">
			<Sidebar collapsible="none" className="rounded-[22px]">
				<SidebarHeader className="flex flex-col p-[20px_18px_14px] gap-y-1">
					<div className="text-xl font-medium tracking-[-0.02em] text-foreground">
						{t('common.skill-hub')}
					</div>
					<div className="text-text-tertiary text-xs">{t('skill.subtitle')}</div>
				</SidebarHeader>
				<SidebarContent>
					<SidebarGroup className="mt-6 px-2 py-0">
						<SidebarGroupLabel>{t('common.mine')}</SidebarGroupLabel>
						<SidebarGroupContent>
							<SidebarMenu>
								<SidebarMenuItem>
									<SidebarMenuButton
										isActive={!hubId}
										onClick={() => navigate('/skill')}
									>
										<Plug />
										<span className="min-w-0 flex-1 truncate">
											{t('common.my-skill')}
										</span>
										<span className="font-mono text-[10px] text-text-data">
											{skills.length}
										</span>
									</SidebarMenuButton>
								</SidebarMenuItem>
							</SidebarMenu>
						</SidebarGroupContent>
					</SidebarGroup>

					<SidebarGroup className="mt-5 px-2 py-0">
						<SidebarGroupLabel className="justify-between">
							{t('skill.hubsLabel')}
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
										<EmptyTitle>{t('skill.loadFailedTitle')}</EmptyTitle>
										<EmptyDescription>
											{t('skill.loadFailedDescription')}
										</EmptyDescription>
									</EmptyHeader>
									<EmptyContent>
										<Button variant="outline" size="sm" onClick={refetch}>
											{t('skill.retry')}
										</Button>
									</EmptyContent>
								</Empty>
							) : hubs.length === 0 ? (
								<Empty className="border-none py-4 min-h-40">
									<EmptyHeader>
										<EmptyMedia variant="icon">
											<Blocks />
										</EmptyMedia>
										<EmptyTitle>{t('skill.noHubsTitle')}</EmptyTitle>
										<EmptyDescription>
											{t('skill.noHubsDescription')}
										</EmptyDescription>
									</EmptyHeader>
								</Empty>
							) : (
								<SidebarMenu>
									{hubs.map((hub) => (
										<SidebarMenuItem key={hub.hub_id}>
											<SidebarMenuButton
												isActive={hubId === hub.hub_id}
												onClick={() => navigate(`/skill/${hub.hub_id}`)}
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
						onInstalled={refetchSkills}
					/>
				) : (
					<MinePanel skills={skills} loading={skillsLoading} onRemove={remove} />
				)}
			</main>
		</div>
	);
}
