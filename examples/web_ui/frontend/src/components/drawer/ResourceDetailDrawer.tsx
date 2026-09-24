import { ExternalLink } from 'lucide-react';

import { Markdown } from '@/components/markdown';
import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar.tsx';
import { Badge } from '@/components/ui/badge.tsx';
import {
	Drawer,
	DrawerContent,
	DrawerDescription,
	DrawerFooter,
	DrawerHeader,
	DrawerTitle,
} from '@/components/ui/drawer.tsx';
import { Separator } from '@/components/ui/separator.tsx';
import { Spinner } from '@/components/ui/spinner.tsx';
import { useTranslation } from '@/i18n/useI18n';

/**
 * What the drawer needs, flattened out of whichever shape it came from — a
 * hub card or an installed record, MCP or skill. They agree on everything
 * shown here, so one drawer serves every listing.
 */
export interface ResourceDetail {
	name: string;
	display_name?: string | null;
	description: string;
	tags: string[];
	version?: string | null;
	hub_id?: string | null;
	author?: string | null;
	icon_url?: string | null;
	url?: string | null;
	updated_at?: number | null;
	installs?: number | null;
	downloads?: number | null;
	markdown?: string | null;
}

interface Props {
	/** The resource to show, or `null` to keep the drawer closed. */
	skill: ResourceDetail | null;
	/** Whether the detail body is still being fetched. */
	loading?: boolean;
	/** The action button, which differs between browsing and the library. */
	action: React.ReactNode;
	/**
	 * The scrolling body: a skill's `SKILL.md`, an MCP's config template.
	 * Falls back to `skill.markdown` when omitted.
	 */
	body?: React.ReactNode;
	onOpenChange: (open: boolean) => void;
}

interface FactProps {
	label: string;
	children: React.ReactNode;
}

/** One label/value row: label left, value right. */
function Fact({ label, children }: FactProps) {
	return (
		<div className="flex items-baseline justify-between gap-x-4 py-0.5">
			<span className="text-xs text-muted-foreground shrink-0">{label}</span>
			<span className="text-xs text-right break-words min-w-0">{children}</span>
		</div>
	);
}

/**
 * The full detail of one hub card or installed resource.
 *
 * The facts sit in the header so they stay put; only the body below is
 * long enough to want scrolling.
 */
export function ResourceDetailDrawer({
	skill,
	loading = false,
	action,
	body,
	onOpenChange,
}: Props) {
	const { t } = useTranslation();

	return (
		<Drawer direction="right" open={skill !== null} onOpenChange={onOpenChange}>
			{/* The base component pins right-side drawers to max-w-sm via a
			    data-attribute selector, so overriding needs the same form. */}
			<DrawerContent className="data-[vaul-drawer-direction=right]:sm:max-w-2xl">
				<DrawerHeader className="shrink-0 gap-2">
					<div className="flex items-center gap-x-3 min-w-0">
						<Avatar className="size-10 rounded-md">
							<AvatarImage
								src={skill?.icon_url ?? undefined}
								alt={skill?.display_name || skill?.name || '?'}
							/>
							<AvatarFallback className="rounded-md">
								{(skill?.display_name || skill?.name || '?')
									.slice(0, 1)
									.toUpperCase()}
							</AvatarFallback>
						</Avatar>
						<div className="flex flex-col min-w-0">
							<DrawerTitle className="truncate">
								{skill?.display_name || skill?.name}
							</DrawerTitle>
							{skill?.author && (
								<span className="text-xs text-muted-foreground truncate">
									@{skill.author}
								</span>
							)}
						</div>
					</div>

					<DrawerDescription>{skill?.description}</DrawerDescription>

					{/* No name row — the drawer title already is the name. */}
					<div className="flex flex-col">
						{skill?.version && (
							<Fact label={t('skill.versionLabel')}>{skill.version}</Fact>
						)}
						{skill?.hub_id && <Fact label={t('skill.hubLabel')}>{skill.hub_id}</Fact>}
						{skill?.updated_at && (
							<Fact label={t('skill.updatedLabel')}>
								{/* Reported in seconds; Date wants millis. */}
								{new Date(skill.updated_at * 1000).toLocaleDateString()}
							</Fact>
						)}
						{/* Explicit null check: a hub that counts installs and
						    reports 0 is saying something, a hub that does not
						    count them at all is not. */}
						{skill?.installs != null && (
							<Fact label={t('skill.installsLabel')}>
								{skill.installs.toLocaleString()}
							</Fact>
						)}
						{skill?.downloads != null && (
							<Fact label={t('skill.downloadsLabel')}>
								{skill.downloads.toLocaleString()}
							</Fact>
						)}
						{skill?.url && (
							<Fact label={t('skill.linkLabel')}>
								<a
									href={skill.url}
									target="_blank"
									rel="noreferrer"
									className="inline-flex items-center gap-x-1 underline underline-offset-2"
								>
									{t('skill.viewOnHub')}
									<ExternalLink className="size-3" />
								</a>
							</Fact>
						)}
					</div>

					{skill && skill.tags.length > 0 && (
						<div className="flex flex-wrap gap-1">
							{skill.tags.map((tag) => (
								<Badge
									key={tag}
									variant="outline"
									className="text-[10px] px-1 py-0"
								>
									{tag}
								</Badge>
							))}
						</div>
					)}
				</DrawerHeader>

				<Separator />

				<div className="flex-1 min-h-0 overflow-y-auto scroll-fade px-4 py-4">
					{loading ? (
						<div className="flex justify-center py-10">
							<Spinner />
						</div>
					) : body ? (
						body
					) : skill?.markdown ? (
						<Markdown>{skill.markdown}</Markdown>
					) : (
						<p className="text-sm text-muted-foreground">{t('skill.noReadme')}</p>
					)}
				</div>

				<DrawerFooter>{action}</DrawerFooter>
			</DrawerContent>
		</Drawer>
	);
}
