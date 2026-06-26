import { PlusCircle, Search, SearchX, Trash, Unplug } from 'lucide-react';
import { useState } from 'react';

import type { MCPClient, MCPClientStatus } from '@/api';
import { DeleteDialog } from '@/components/dialog/DeleteDialog.tsx';
import { CreateMCPDialog } from '@/components/dialog/MCPDialog.tsx';
import { PanelEmpty } from '@/components/panel/PanelEmpty';
import { Button } from '@/components/ui/button';
import { InputGroup, InputGroupAddon, InputGroupInput } from '@/components/ui/input-group';
import { Item, ItemActions, ItemContent, ItemDescription, ItemTitle } from '@/components/ui/item';
import { Kbd, KbdGroup } from '@/components/ui/kbd';
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
	/**
	 * Remove an MCP server by name.
	 *
	 * @param name - The MCP server name to remove.
	 */
	onRemove: (name: string) => Promise<void>;
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
 * @param onAdd - Add-MCP callback.
 * @param onRemove - Remove-MCP callback.
 * @returns The MCP panel body.
 */
export function McpPanel({ mcps, loading = false, onAdd, onRemove }: McpPanelProps) {
	const { t } = useTranslation();
	const [search, setSearch] = useState('');
	const [deleteOpen, setDeleteOpen] = useState(false);
	const [deleteTarget, setDeleteTarget] = useState<string | null>(null);

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
				<div className="flex flex-col flex-1 min-h-0 overflow-y-auto gap-y-2">
					{filtered.map((mcp) => (
						<Item key={mcp.name} variant="outline">
							<ItemContent>
								<ItemTitle className="flex items-center gap-x-2">
									<span
										className={`size-2 shrink-0 rounded-full ${mcp.is_healthy ? 'bg-green-500' : 'bg-red-500'}`}
									/>
									{mcp.name}
								</ItemTitle>
								<ItemDescription>
									<KbdGroup>
										<Kbd>
											{mcp.mcp_config.type === 'stdio_mcp' ? 'STDIO' : 'HTTP'}
										</Kbd>
										<Kbd>
											{t('panel.mcp.tools', { count: mcp.tools.length })}
										</Kbd>
									</KbdGroup>
								</ItemDescription>
							</ItemContent>
							<ItemActions>
								<Button
									variant="outline"
									size="icon-sm"
									onClick={() => {
										setDeleteTarget(mcp.name);
										setDeleteOpen(true);
									}}
								>
									<Trash />
								</Button>
							</ItemActions>
						</Item>
					))}
				</div>
			)}

			<CreateMCPDialog onAdd={onAdd}>
				<Button variant="default">
					<PlusCircle />
					{t('panel.mcp.add')}
				</Button>
			</CreateMCPDialog>

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
