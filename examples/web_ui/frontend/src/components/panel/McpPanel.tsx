import {
	ChevronRight,
	PlusCircle,
	Search,
	SearchX,
	Trash,
	TriangleAlert,
	Unplug,
} from 'lucide-react';
import { useState } from 'react';

import type { MCPClient, MCPClientStatus, MCPView } from '@/api';
import { AddMCPDialog } from '@/components/dialog/AddMCPDialog.tsx';
import { DeleteDialog } from '@/components/dialog/DeleteDialog.tsx';
import { PanelEmpty } from '@/components/panel/PanelEmpty';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert.tsx';
import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar.tsx';
import { Button } from '@/components/ui/button';
import {
	Collapsible,
	CollapsibleContent,
	CollapsibleTrigger,
} from '@/components/ui/collapsible.tsx';
import { InputGroup, InputGroupAddon, InputGroupInput } from '@/components/ui/input-group';
import {
	Item,
	ItemActions,
	ItemContent,
	ItemDescription,
	ItemFooter,
	ItemMedia,
	ItemTitle,
} from '@/components/ui/item';
import { useMCPs } from '@/hooks/useMCPs.ts';
import { useTranslation } from '@/i18n/useI18n.ts';

interface McpPanelProps {
	/** The MCP servers equipped in the workspace. */
	mcps: MCPClientStatus[];
	/** Whether the MCP list is still loading. */
	loading?: boolean;
	/**
	 * Add one or more MCP servers to the workspace.
	 *
	 * @param mcps - The MCP client configs to add.
	 */
	onAdd: (mcps: MCPClient[]) => Promise<void>;
	onAddFromLibrary: (mcpIds: string[]) => Promise<void>;
	/**
	 * Remove an MCP server by name.
	 *
	 * @param name - The MCP server name to remove.
	 */
	onRemove: (name: string) => Promise<void>;
}

interface McpRowProps {
	mcp: MCPClientStatus;
	/**
	 * The same MCP in the user's library, matched by name. The workspace
	 * stores only what it needs to connect, so the icon, author and
	 * description come from there — and are absent for one that was never
	 * installed through the library.
	 */
	installed?: MCPView;
	onDelete: () => void;
}

/**
 * One MCP in the workspace: what it is on top, what it gives you below.
 *
 * The tool list is worth a click but not the space — it is collapsed
 * behind a count. A failed MCP has no tools to show, so it gets the
 * reason instead.
 */
function McpRow({ mcp, installed, onDelete }: McpRowProps) {
	const { t } = useTranslation();
	const [open, setOpen] = useState(false);

	return (
		<Item variant="outline" className="group/mcp">
			<ItemMedia>
				<Avatar className="rounded-md">
					<AvatarImage
						src={installed?.icon_url ?? undefined}
						alt={mcp.name}
						loading="lazy"
					/>
					<AvatarFallback className="rounded-md">
						{mcp.name.slice(0, 1).toUpperCase()}
					</AvatarFallback>
				</Avatar>
			</ItemMedia>

			<ItemContent>
				<ItemTitle>
					<div className="truncate font-medium">{mcp.name}</div>
					{installed?.author && (
						<span className="text-xs text-muted-foreground">@{installed.author}</span>
					)}
					<span className="text-xs text-muted-foreground/50">
						{mcp.mcp_config.type === 'stdio_mcp' ? '#stdio' : '#http'}
					</span>
				</ItemTitle>
				{installed?.description && (
					<ItemDescription className="line-clamp-1">
						{installed.description}
					</ItemDescription>
				)}
			</ItemContent>

			<ItemActions>
				<span
					className={`size-2 shrink-0 rounded-full ${mcp.is_healthy ? 'bg-green-500' : 'bg-red-500'}`}
					title={t(mcp.is_healthy ? 'panel.mcp.healthy' : 'panel.mcp.unhealthy')}
				/>
				{/* Only on hover: deleting is rare, and a delete button on
				    every row competes with the status for attention. */}
				<Button
					variant="secondary"
					size="icon-sm"
					onClick={onDelete}
					title={t('common.delete')}
				>
					<Trash className="size-3" />
				</Button>
			</ItemActions>

			<ItemFooter className="min-w-0">
				{mcp.is_healthy ? (
					<Collapsible open={open} onOpenChange={setOpen} className="min-w-0 basis-full">
						<CollapsibleTrigger asChild>
							<Button
								variant={'secondary'}
								className="w-full justify-between text-muted-foreground"
								size="sm"
							>
								{t('panel.mcp.tools', { count: mcp.tools.length })}
								<ChevronRight
									className={`shrink-0 transition-transform ${open ? 'rotate-90' : ''}`}
								/>
							</Button>
						</CollapsibleTrigger>
						<CollapsibleContent className="flex w-full flex-col gap-y-1 px-3 py-2">
							{mcp.tools.map((tool) => (
								<div key={tool.name} className="flex min-w-0 flex-col gap-y-0.5">
									{/* Names run long and are unbroken, so they must
								    be allowed to shrink before truncating. */}
									<span
										className="min-w-0 truncate font-mono text-xs"
										title={tool.name}
									>
										{tool.name}
									</span>
									{tool.description && (
										<span className="line-clamp-2 text-xs text-muted-foreground">
											{tool.description}
										</span>
									)}
								</div>
							))}
						</CollapsibleContent>
					</Collapsible>
				) : (
					<Alert
						variant="destructive"
						className="basis-full border-red-200 bg-red-50 text-destructive dark:border-red-900 dark:bg-red-950 dark:text-red-50"
					>
						<TriangleAlert />
						<AlertTitle>{t('panel.mcp.unhealthy')}</AlertTitle>
						{mcp.error && (
							<AlertDescription className="break-words">{mcp.error}</AlertDescription>
						)}
					</Alert>
				)}
			</ItemFooter>
		</Item>
	);
}

/**
 * Pure content body for the MCP dock panel: a search box, the list of
 * equipped MCP servers, and an "Add MCP" action. Holds only local UI
 * state (search text, delete confirmation target); all data arrives
 * via props so it owns no data fetching.
 *
 * Renders without its own header/border — the surrounding `Panel`
 * chrome (from `PanelDock`) provides those.
 *
 * @param mcps - The MCP servers to list.
 * @param loading - Whether the list is loading.
 * @param onAdd - Add-MCP callback for a hand-written config.
 * @param onAddFromLibrary - Add-MCP callback for library picks.
 * @param onRemove - Remove-MCP callback.
 * @returns The MCP panel body.
 */
export function McpPanel({
	mcps,
	loading = false,
	onAdd,
	onAddFromLibrary,
	onRemove,
}: McpPanelProps) {
	const { t } = useTranslation();
	const [search, setSearch] = useState('');
	const [deleteOpen, setDeleteOpen] = useState(false);
	const [deleteTarget, setDeleteTarget] = useState<string | null>(null);
	// The workspace stores only what it takes to connect, so the display
	// fields are looked up in the library, matched on the shared name.
	const { mcps: library } = useMCPs();
	const byName = new Map(library.map((mcp) => [mcp.name, mcp]));

	const filtered = search
		? mcps.filter((m) => m.name.toLowerCase().includes(search.toLowerCase()))
		: mcps;

	return (
		<div className="flex flex-col flex-1 min-h-0 gap-y-2">
			<span className="text-muted-foreground text-sm">{t('panel.mcp.description')}</span>
			<InputGroup>
				<InputGroupInput
					placeholder={t('panel.mcp.searchPlaceholder')}
					value={search}
					onChange={(e) => setSearch(e.target.value)}
				/>
				<InputGroupAddon align="inline-end">
					<Search />
				</InputGroupAddon>
			</InputGroup>

			{loading ? (
				<div className="flex flex-1 items-center justify-center">
					<p className="text-muted-foreground text-sm">{t('panel.loading')}</p>
				</div>
			) : filtered.length === 0 ? (
				<PanelEmpty
					icon={search ? SearchX : Unplug}
					title={search ? t('panel.search.emptyTitle') : t('panel.mcp.emptyTitle')}
					description={
						search
							? t('panel.search.emptyDescription', { query: search })
							: t('panel.mcp.emptyDescription')
					}
				/>
			) : (
				<div className="flex flex-col flex-1 min-h-0 gap-y-2 overflow-y-auto scroll-fade-y">
					{filtered.map((mcp) => (
						<McpRow
							key={mcp.name}
							mcp={mcp}
							installed={byName.get(mcp.name)}
							onDelete={() => {
								setDeleteTarget(mcp.name);
								setDeleteOpen(true);
							}}
						/>
					))}
				</div>
			)}

			<AddMCPDialog
				present={new Set(mcps.map((m) => m.name))}
				onAdd={onAdd}
				onAddFromLibrary={onAddFromLibrary}
			>
				<Button variant="default">
					<PlusCircle />
					{t('panel.mcp.add')}
				</Button>
			</AddMCPDialog>

			<DeleteDialog
				open={deleteOpen}
				onOpenChange={setDeleteOpen}
				title={t('common.deleteTitle', {
					entity: t('dialog-mcp-delete.entity'),
					name: deleteTarget ?? '',
				})}
				description={t('common.deleteDescription')}
				onConfirm={async () => {
					if (deleteTarget) await onRemove(deleteTarget);
				}}
			/>
		</div>
	);
}
