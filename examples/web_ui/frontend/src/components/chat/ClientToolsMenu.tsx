import { Braces, ChevronRight, Monitor, Pencil, Plus, Trash2, Wrench } from 'lucide-react';
import { useState, useSyncExternalStore } from 'react';

import { ClientToolEditorDialog } from './ClientToolEditorDialog';
import { DeleteDialog } from '@/components/dialog/DeleteDialog';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
	Popover,
	PopoverContent,
	PopoverDescription,
	PopoverHeader,
	PopoverTitle,
	PopoverTrigger,
} from '@/components/ui/popover';
import {
	Sheet,
	SheetContent,
	SheetDescription,
	SheetHeader,
	SheetTitle,
	SheetTrigger,
} from '@/components/ui/sheet';
import { Switch } from '@/components/ui/switch';
import { useIsMobile } from '@/hooks/use-mobile';
import { useTranslation } from '@/i18n/useI18n';
import {
	deleteCustomClientExternalTool,
	getClientExternalToolServerSnapshot,
	getClientExternalToolStoreSnapshot,
	getEnabledClientExternalToolNames,
	setClientExternalToolEnabled,
	subscribeClientExternalToolStore,
	type CustomClientExternalTool,
} from '@/lib/client-external-tool-store';
import { CLIENT_EXTERNAL_TOOL_REGISTRY } from '@/lib/client-external-tools';

type ToolEntry =
	| {
			kind: 'built-in';
			name: string;
			displayName: string;
			description: string;
			inputSchema: Record<string, unknown>;
	  }
	| {
			kind: 'custom';
			name: string;
			displayName: string;
			description: string;
			inputSchema: Record<string, unknown>;
			tool: CustomClientExternalTool;
	  };

function ClientToolsContent({
	agentId,
	sessionId,
	onAdd,
	onEdit,
	onDelete,
}: {
	agentId: string | null;
	sessionId: string | null;
	onAdd: () => void;
	onEdit: (tool: CustomClientExternalTool) => void;
	onDelete: (tool: CustomClientExternalTool) => void;
}) {
	const { t } = useTranslation();
	const state = useSyncExternalStore(
		subscribeClientExternalToolStore,
		getClientExternalToolStoreSnapshot,
		getClientExternalToolServerSnapshot,
	);
	const enabledNames = new Set(getEnabledClientExternalToolNames(agentId, sessionId, state));
	const entries: ToolEntry[] = [
		...CLIENT_EXTERNAL_TOOL_REGISTRY.map((registration) => ({
			kind: 'built-in' as const,
			name: registration.definition.name,
			displayName: t(registration.displayNameKey),
			description: t(registration.displayDescriptionKey),
			inputSchema: registration.definition.input_schema,
		})),
		...state.customTools.map((tool) => ({
			kind: 'custom' as const,
			name: tool.definition.name,
			displayName: tool.display_name,
			description: tool.definition.description,
			inputSchema: tool.definition.input_schema,
			tool,
		})),
	];
	const canSelect = Boolean(agentId && sessionId);

	return (
		<div className="space-y-3 px-1 pb-1">
			<div className="flex items-center justify-between gap-3">
				<p className="text-xs leading-5 text-muted-foreground">
					{canSelect ? t('clientTools.selectionNote') : t('clientTools.noSessionNote')}
				</p>
				<Button type="button" variant="outline" size="sm" onClick={onAdd}>
					<Plus className="size-3.5" />
					{t('clientTools.add')}
				</Button>
			</div>

			<div className="space-y-2">
				{entries.map((entry) => {
					const enabled = enabledNames.has(entry.name);
					return (
						<div
							key={entry.name}
							className="overflow-hidden rounded-xl bg-muted/60 ring-1 ring-border/70"
						>
							<div className="flex items-start gap-3 px-3 py-3">
								<div className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-background text-muted-foreground ring-1 ring-border">
									<Monitor className="size-4" />
								</div>
								<div className="min-w-0 flex-1">
									<div className="flex flex-wrap items-center gap-2">
										<span className="font-medium text-foreground">
											{entry.displayName}
										</span>
										<Badge
											variant="outline"
											className="bg-background font-normal"
										>
											{t(
												entry.kind === 'built-in'
													? 'clientTools.builtInBadge'
													: 'clientTools.customBadge',
											)}
										</Badge>
									</div>
									<code className="mt-1 block truncate text-[11px] text-muted-foreground">
										{entry.name}
									</code>
									<p className="mt-1 text-xs leading-5 text-muted-foreground">
										{entry.description}
									</p>
								</div>
								<div className="flex shrink-0 items-center gap-1">
									{entry.kind === 'custom' ? (
										<>
											<Button
												type="button"
												variant="ghost"
												size="icon-sm"
												tooltip={t('common.edit')}
												onClick={() => onEdit(entry.tool)}
											>
												<Pencil />
											</Button>
											<Button
												type="button"
												variant="ghost"
												size="icon-sm"
												tooltip={t('common.delete')}
												onClick={() => onDelete(entry.tool)}
											>
												<Trash2 />
											</Button>
										</>
									) : null}
									<Switch
										checked={enabled}
										disabled={!canSelect}
										aria-label={t('clientTools.toggleLabel', {
											name: entry.displayName,
										})}
										onCheckedChange={(checked) => {
											if (agentId && sessionId) {
												setClientExternalToolEnabled(
													agentId,
													sessionId,
													entry.name,
													checked,
												);
											}
										}}
									/>
								</div>
							</div>

							<details className="group border-t border-border/70">
								<summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2 text-xs font-medium text-muted-foreground outline-none hover:bg-muted focus-visible:ring-3 focus-visible:ring-ring/50 [&::-webkit-details-marker]:hidden">
									<Braces className="size-3.5" />
									{t('clientTools.inputSchema')}
									<ChevronRight className="ml-auto size-3.5 transition-transform group-open:rotate-90" />
								</summary>
								<div className="px-3 pb-3">
									<pre className="max-h-56 overflow-auto rounded-lg bg-background p-3 font-mono text-[11px] leading-5 text-secondary-foreground ring-1 ring-border">
										{JSON.stringify(entry.inputSchema, null, 2)}
									</pre>
								</div>
							</details>
						</div>
					);
				})}
			</div>

			<p className="px-1 text-xs leading-5 text-muted-foreground">
				{t('clientTools.availabilityNote')}
			</p>
		</div>
	);
}

export function ClientToolsMenu({
	agentId,
	sessionId,
}: {
	agentId: string | null;
	sessionId: string | null;
}) {
	const { t } = useTranslation();
	const isMobile = useIsMobile();
	const state = useSyncExternalStore(
		subscribeClientExternalToolStore,
		getClientExternalToolStoreSnapshot,
		getClientExternalToolServerSnapshot,
	);
	const enabledCount = getEnabledClientExternalToolNames(agentId, sessionId, state).length;
	const totalCount = CLIENT_EXTERNAL_TOOL_REGISTRY.length + state.customTools.length;
	const [editorTool, setEditorTool] = useState<CustomClientExternalTool | null | undefined>();
	const [deleteTool, setDeleteTool] = useState<CustomClientExternalTool | null>(null);
	const content = (
		<ClientToolsContent
			agentId={agentId}
			sessionId={sessionId}
			onAdd={() => setEditorTool(null)}
			onEdit={setEditorTool}
			onDelete={setDeleteTool}
		/>
	);
	const trigger = (
		<Button
			type="button"
			variant="secondary"
			size="sm"
			className="gap-1.5"
			aria-label={t('clientTools.triggerLabel', {
				enabled: enabledCount,
				total: totalCount,
			})}
		>
			<Wrench className="size-3.5" />
			<span className="hidden sm:inline">{t('clientTools.trigger')}</span>
			<Badge variant="outline" className="h-4 min-w-4 bg-background px-1 text-[10px]">
				{enabledCount}/{totalCount}
			</Badge>
		</Button>
	);

	return (
		<>
			{isMobile ? (
				<Sheet>
					<SheetTrigger asChild>{trigger}</SheetTrigger>
					<SheetContent
						side="bottom"
						className="max-h-[85vh] rounded-t-[24px] px-4 pb-5 pt-1"
					>
						<SheetHeader className="px-1 pb-2 pt-4 text-left">
							<SheetTitle>{t('clientTools.title')}</SheetTitle>
							<SheetDescription>{t('clientTools.description')}</SheetDescription>
						</SheetHeader>
						<div className="overflow-y-auto">{content}</div>
					</SheetContent>
				</Sheet>
			) : (
				<Popover>
					<PopoverTrigger asChild>{trigger}</PopoverTrigger>
					<PopoverContent align="end" side="top" className="max-h-[70vh] w-[26rem] p-3">
						<PopoverHeader className="px-1 pb-2">
							<PopoverTitle className="flex items-center gap-2">
								<Wrench className="size-4 text-muted-foreground" />
								{t('clientTools.title')}
							</PopoverTitle>
							<PopoverDescription>{t('clientTools.description')}</PopoverDescription>
						</PopoverHeader>
						<div className="max-h-[calc(70vh-5rem)] overflow-y-auto">{content}</div>
					</PopoverContent>
				</Popover>
			)}

			{editorTool !== undefined ? (
				<ClientToolEditorDialog
					key={editorTool?.definition.name ?? 'new-client-tool'}
					tool={editorTool}
					agentId={agentId}
					sessionId={sessionId}
					onClose={() => setEditorTool(undefined)}
				/>
			) : null}

			<DeleteDialog
				open={deleteTool !== null}
				onOpenChange={(open) => !open && setDeleteTool(null)}
				title={t('clientTools.deleteTitle', { name: deleteTool?.display_name ?? '' })}
				description={t('clientTools.deleteDescription')}
				confirmLabel={t('common.delete')}
				onConfirm={async () => {
					if (deleteTool) deleteCustomClientExternalTool(deleteTool.definition.name);
				}}
			/>
		</>
	);
}
