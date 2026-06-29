import { Ellipsis, Files, FlaskConical, Pencil, Plus, Trash2 } from 'lucide-react';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate, useParams } from 'react-router-dom';

import type { KnowledgeBaseView } from '@/api';
import { CreateCredentialDialog } from '@/components/dialog/CreateCredentialDialog.tsx';
import { CreateKnowledgeBaseDialog } from '@/components/dialog/CreateKnowledgeBaseDialog.tsx';
import { DeleteDialog } from '@/components/dialog/DeleteDialog.tsx';
import { EditKnowledgeBaseDialog } from '@/components/dialog/EditKnowledgeBaseDialog.tsx';
import { KnowledgeSearchDrawer } from '@/components/drawer/KnowledgeSearchDrawer.tsx';
import { KnowledgeDocumentsPanel } from '@/components/knowledge/KnowledgeDocumentsPanel.tsx';
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
	SidebarGroupAction,
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
		<div className="flex flex-col gap-y-6 p-6 overflow-y-auto h-full">
			{/* Header */}
			<div className="flex items-start justify-between gap-x-4">
				<div className="flex flex-col gap-y-1 min-w-0">
					<h2 className="text-lg font-semibold truncate">{knowledgeBase.name}</h2>
					{knowledgeBase.description ? (
						<p className="text-sm text-muted-foreground">{knowledgeBase.description}</p>
					) : null}
				</div>
				<div className="flex items-center gap-x-2 shrink-0">
					<Button size="sm" variant="outline" onClick={onTest}>
						<FlaskConical className="size-3.5" />
						{t('knowledge.test.button')}
					</Button>
				</div>
			</div>

			<Separator />

			{/* Documents */}
			<KnowledgeDocumentsPanel knowledgeBaseId={knowledgeBase.id} />
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
		<div className="flex size-full">
			<Sidebar collapsible="none" className="border-r">
				<SidebarHeader className={'flex flex-col mt-5 gap-y-1'}>
					<div className="text-lg font-semibold">{t('common.knowledge')}</div>
					<div className="text-muted-foreground text-xs">{t('knowledge.subtitle')}</div>
				</SidebarHeader>
				<SidebarContent className="my-5">
					<SidebarGroup>
						<SidebarGroupLabel>{t('knowledge.list.label')}</SidebarGroupLabel>
						<SidebarGroupAction>
							<Button
								size="icon-xs"
								variant="default"
								onClick={() => setCreateDialogOpen(true)}
							>
								<Plus />
							</Button>
						</SidebarGroupAction>
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
													<span className="truncate">{kb.name}</span>
												</SidebarMenuButton>
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
																onClick={() => setEditTarget(kb)}
															>
																<Pencil />
																{t('common.edit')}
															</DropdownMenuItem>
															<DropdownMenuItem
																variant="destructive"
																onClick={() => setDeleteTarget(kb)}
															>
																<Trash2 />
																{t('common.delete')}
															</DropdownMenuItem>
														</DropdownMenuContent>
													</DropdownMenu>
												</SidebarMenuAction>
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
			<main className="flex-1 min-h-0 overflow-hidden">
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
