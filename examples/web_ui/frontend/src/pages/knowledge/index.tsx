import { format } from 'date-fns';
import { Ellipsis, Files, FlaskConical, Pencil, Plus, Trash2 } from 'lucide-react';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate, useParams } from 'react-router-dom';

import type { KnowledgeBaseView } from '@/api';
import { CreateCredentialDialog } from '@/components/dialog/CreateCredentialDialog.tsx';
import { CreateKnowledgeBaseDialog } from '@/components/dialog/CreateKnowledgeBaseDialog.tsx';
import { DeleteDialog } from '@/components/dialog/DeleteDialog.tsx';
import { EditKnowledgeBaseDialog } from '@/components/dialog/EditKnowledgeBaseDialog.tsx';
import { KnowledgeSearchDrawer } from '@/components/drawer/KnowledgeSearchDrawer.tsx';
import { KnowledgeDocumentsPanel } from '@/components/knowledge/KnowledgeDocumentsPanel.tsx';
import { Badge } from '@/components/ui/badge.tsx';
import { Button } from '@/components/ui/button.tsx';
import {
	DropdownMenu,
	DropdownMenuContent,
	DropdownMenuItem,
	DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu.tsx';
import {
	Empty,
	EmptyContent,
	EmptyDescription,
	EmptyHeader,
	EmptyMedia,
	EmptyTitle,
} from '@/components/ui/empty.tsx';
import { Separator } from '@/components/ui/separator.tsx';
import {
	Sidebar,
	SidebarContent,
	SidebarFooter,
	SidebarGroup,
	SidebarGroupContent,
	SidebarGroupLabel,
	SidebarHeader,
	SidebarMenu,
	SidebarMenuAction,
	SidebarMenuButton,
	SidebarMenuItem,
} from '@/components/ui/sidebar.tsx';
import { useKnowledgeBases } from '@/hooks/useKnowledgeBases.ts';

interface DetailPanelProps {
	knowledgeBase?: KnowledgeBaseView;
	onTest?: () => void;
}

function DetailPanel({ knowledgeBase, onTest }: DetailPanelProps) {
	const { t } = useTranslation();

	if (!knowledgeBase) {
		return (
			<div className="flex h-full items-center justify-center">
				<Empty className="border-none">
					<EmptyHeader>
						<EmptyTitle>{t('knowledge.selectHint')}</EmptyTitle>
						<EmptyDescription>{t('knowledge.selectHintDescription')}</EmptyDescription>
					</EmptyHeader>
				</Empty>
			</div>
		);
	}

	return (
		<div className="flex h-full flex-col">
			{/* Header */}
			<div className="shrink-0 flex items-start justify-between gap-x-4 p-[18px_18px_16px]">
				<div className="flex flex-col gap-y-1 min-w-0">
					<div className="flex items-center gap-x-2">
						<span className="truncate text-lg font-medium tracking-[-0.015em] text-foreground">
							{knowledgeBase.name}
						</span>
						{!knowledgeBase.editable && (
							<Badge variant="secondary" title={t('common.readOnlyTooltip')}>
								{t('common.readOnly')}
							</Badge>
						)}
					</div>
					{knowledgeBase.description ? (
						<p className="text-sm text-text-data">{knowledgeBase.description}</p>
					) : null}
				</div>
				<div className="flex items-center gap-x-2 shrink-0">
					<Button size="sm" variant="outline" onClick={onTest}>
						<FlaskConical className="size-3.5" />
						{t('knowledge.test.button')}
					</Button>
				</div>
			</div>

			<Separator className="shrink-0" />

			{/* Configuration + documents */}
			<div className="min-h-0 flex-1 overflow-y-auto p-[20px_18px_24px]">
				<div className="flex flex-col gap-y-6">
					<ConfigCard knowledgeBase={knowledgeBase} />
					<KnowledgeDocumentsPanel knowledgeBaseId={knowledgeBase.id} />
				</div>
			</div>
		</div>
	);
}

function ConfigItem({ label, value }: { label: string; value: string }) {
	return (
		<div className="flex min-w-0 flex-col gap-y-0.5">
			<span className="text-muted-foreground text-xs">{label}</span>
			<span className="truncate text-sm text-foreground" title={value}>
				{value}
			</span>
		</div>
	);
}

/**
 * Read-only card surfacing the full configuration pinned at creation
 * time — embedding model, chunker, credential — plus the aggregated
 * counts the list endpoint now serves (#2360).
 */
function ConfigCard({ knowledgeBase }: { knowledgeBase: KnowledgeBaseView }) {
	const { t } = useTranslation();
	const embedding = knowledgeBase.embedding_model_config;
	const chunker = knowledgeBase.chunker_config;

	// Only worth showing when something is not ready — a KB where every
	// document indexed cleanly says nothing extra beyond the totals.
	const counts = knowledgeBase.status_counts;
	const unfinished = counts.pending + counts.parsing + counts.chunking + counts.indexing;
	const statusValue =
		counts.error > 0 || unfinished > 0
			? t('knowledge.config.statusValue', {
					ready: counts.ready,
					indexing: unfinished,
					failed: counts.error,
				})
			: null;

	const chunkerValue = chunker
		? Object.keys(chunker.parameters).length > 0
			? `${chunker.type} · ${Object.entries(chunker.parameters)
					.map(([k, v]) => `${k}=${String(v)}`)
					.join(', ')}`
			: chunker.type
		: '—';

	return (
		<div className="flex flex-col gap-y-3">
			<h3 className="text-[13.5px] font-medium text-foreground">
				{t('knowledge.config.title')}
			</h3>
			<div className="border-border bg-card grid grid-cols-2 gap-x-4 gap-y-3 rounded-lg border p-3 sm:grid-cols-3">
				<ConfigItem label={t('knowledge.config.embeddingModel')} value={embedding.model} />
				<ConfigItem
					label={t('knowledge.config.dimensions')}
					value={String(embedding.dimensions)}
				/>
				<ConfigItem
					label={t('knowledge.config.credential')}
					value={knowledgeBase.credential_name ?? embedding.credential_id}
				/>
				<ConfigItem label={t('knowledge.config.chunker')} value={chunkerValue} />
				<ConfigItem
					label={t('knowledge.config.counts')}
					value={t('knowledge.config.countsValue', {
						documents: knowledgeBase.document_count,
						chunks: knowledgeBase.chunk_count,
					})}
				/>
				{statusValue && (
					<ConfigItem label={t('knowledge.config.status')} value={statusValue} />
				)}
				<ConfigItem
					label={t('knowledge.config.createdAt')}
					value={format(new Date(knowledgeBase.created_at), 'yyyy-MM-dd HH:mm')}
				/>
			</div>
		</div>
	);
}

export const KnowledgePage = () => {
	const navigate = useNavigate();
	const { kbId: urlKbId } = useParams<{ kbId?: string }>();
	const { t } = useTranslation();

	const { knowledgeBases, remove, refetch } = useKnowledgeBases();
	const [selectedKbId, setSelectedKbId] = useState<string | undefined>(urlKbId);
	const [createDialogOpen, setCreateDialogOpen] = useState(false);
	const [credentialOpen, setCredentialOpen] = useState(false);
	const [credentialRefetchTrigger, setCredentialRefetchTrigger] = useState(0);

	const [editTarget, setEditTarget] = useState<KnowledgeBaseView | null>(null);
	const [deleteTarget, setDeleteTarget] = useState<KnowledgeBaseView | null>(null);
	const [testOpen, setTestOpen] = useState(false);

	const selectedKb = knowledgeBases.find((kb) => kb.id === selectedKbId);

	// Default to the first knowledge base when none is selected (or the
	// selected one no longer exists), mirroring the URL so refresh keeps it.
	useEffect(() => {
		if (knowledgeBases.length === 0) return;
		if (knowledgeBases.some((kb) => kb.id === selectedKbId)) return;
		const first = knowledgeBases[0].id;
		setSelectedKbId(first);
		navigate(`/knowledge/${first}`, { replace: true });
	}, [knowledgeBases, selectedKbId, navigate]);

	const handleCreated = async (knowledgeBaseId: string) => {
		await refetch();
		setSelectedKbId(knowledgeBaseId);
		navigate(`/knowledge/${knowledgeBaseId}`);
	};

	const handleConfirmDelete = async () => {
		if (!deleteTarget) return;
		const id = deleteTarget.id;
		await remove(id);
		await refetch();
		if (selectedKbId === id) {
			setSelectedKbId(undefined);
			navigate('/knowledge');
		}
	};

	return (
		<div className="flex size-full p-2 gap-2">
			<Sidebar collapsible="none" className="rounded-[22px]">
				<SidebarHeader className={'flex flex-col p-[20px_18px_14px] gap-y-1'}>
					<div className="text-xl font-medium tracking-[-0.02em] text-foreground">
						{t('common.knowledge')}
					</div>
					<div className="text-text-tertiary text-xs">{t('knowledge.subtitle')}</div>
				</SidebarHeader>
				<SidebarContent>
					<SidebarGroup className="mt-6 px-2 py-0">
						<SidebarGroupLabel className="justify-between">
							{t('knowledge.list.label')}
							<Button
								variant="ghost"
								size="icon-xs"
								onClick={() => setCreateDialogOpen(true)}
								title={t('knowledge.list.createButton')}
							>
								<Plus className="size-3.5" />
							</Button>
						</SidebarGroupLabel>
						<SidebarGroupContent>
							{knowledgeBases.length === 0 ? (
								<Empty className="border-none py-4 min-h-50">
									<EmptyHeader>
										<EmptyMedia variant="icon">
											<Files />
										</EmptyMedia>
										<EmptyTitle>{t('knowledge.list.emptyTitle')}</EmptyTitle>
										<EmptyDescription>
											{t('knowledge.list.emptyDescription')}
										</EmptyDescription>
									</EmptyHeader>
									<EmptyContent>
										<Button
											variant="outline"
											size="sm"
											onClick={() => setCreateDialogOpen(true)}
										>
											<Plus />
											{t('knowledge.list.createButton')}
										</Button>
									</EmptyContent>
								</Empty>
							) : (
								<SidebarMenu>
									{knowledgeBases.map((kb) => {
										return (
											<SidebarMenuItem key={kb.id}>
												<SidebarMenuButton
													isActive={urlKbId === kb.id}
													onClick={() => {
														setSelectedKbId(kb.id);
														navigate(`/knowledge/${kb.id}`);
													}}
												>
													<span className="min-w-0 flex-1 truncate">
														{kb.name}
													</span>
													{!kb.editable && (
														<Badge
															variant="secondary"
															className="text-[10px] px-1 py-0"
															title={t('common.readOnlyTooltip')}
														>
															{t('common.readOnly')}
														</Badge>
													)}
												</SidebarMenuButton>
												{kb.editable && (
													<SidebarMenuAction showOnHover>
														<DropdownMenu>
															<DropdownMenuTrigger asChild>
																<Ellipsis />
															</DropdownMenuTrigger>
															<DropdownMenuContent
																side="right"
																align="start"
															>
																<DropdownMenuItem
																	onClick={() =>
																		setEditTarget(kb)
																	}
																>
																	<Pencil />
																	{t('common.edit')}
																</DropdownMenuItem>
																<DropdownMenuItem
																	variant="destructive"
																	onClick={() =>
																		setDeleteTarget(kb)
																	}
																>
																	<Trash2 />
																	{t('common.delete')}
																</DropdownMenuItem>
															</DropdownMenuContent>
														</DropdownMenu>
													</SidebarMenuAction>
												)}
											</SidebarMenuItem>
										);
									})}
								</SidebarMenu>
							)}
						</SidebarGroupContent>
					</SidebarGroup>
				</SidebarContent>
				<SidebarFooter />
			</Sidebar>
			<main className="flex-1 min-h-0 overflow-hidden rounded-[22px] bg-card shadow-panel">
				<DetailPanel knowledgeBase={selectedKb} onTest={() => setTestOpen(true)} />
			</main>
			<CreateKnowledgeBaseDialog
				open={createDialogOpen}
				onOpenChange={setCreateDialogOpen}
				onCreated={handleCreated}
				onAddCredential={() => setCredentialOpen(true)}
				credentialRefetchTrigger={credentialRefetchTrigger}
			/>
			<CreateCredentialDialog
				open={credentialOpen}
				onOpenChange={setCredentialOpen}
				onCreated={() => setCredentialRefetchTrigger((n) => n + 1)}
			/>
			<EditKnowledgeBaseDialog
				open={editTarget !== null}
				onOpenChange={(open) => {
					if (!open) setEditTarget(null);
				}}
				knowledgeBase={editTarget}
				onUpdated={() => refetch()}
			/>
			<DeleteDialog
				open={deleteTarget !== null}
				onOpenChange={(open) => {
					if (!open) setDeleteTarget(null);
				}}
				title={t('dialog-knowledge-base-delete.title')}
				description={t('dialog-knowledge-base-delete.description', {
					name: deleteTarget?.name ?? '',
				})}
				onConfirm={handleConfirmDelete}
			/>
			{selectedKb && (
				<KnowledgeSearchDrawer
					open={testOpen}
					onOpenChange={setTestOpen}
					knowledgeBaseId={selectedKb.id}
					knowledgeBaseName={selectedKb.name}
				/>
			)}
		</div>
	);
};
