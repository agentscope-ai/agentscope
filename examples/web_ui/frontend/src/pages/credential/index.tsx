import { Eye, EyeOff, Plus, Pencil, Trash2 } from 'lucide-react';
import { useState, useEffect, useCallback } from 'react';

import { credentialApi, modelApi, ttsModelApi } from '@/api';
import type { CredentialView, CredentialSchema, ModelCard, TTSModelCard } from '@/api';
import { InputTypeBadges } from '@/components/badge/InputTypeBadges';
import { CreateCredentialDialog } from '@/components/dialog/CreateCredentialDialog';
import { DeleteDialog } from '@/components/dialog/DeleteDialog';
import { EditCredentialDialog } from '@/components/dialog/EditCredentialDialog';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardAction, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from '@/components/ui/empty';
import { Separator } from '@/components/ui/separator';
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
} from '@/components/ui/sidebar';
import { Skeleton } from '@/components/ui/skeleton';
import { useCredentials } from '@/hooks/useCredentials';
import { useTranslation } from '@/i18n/useI18n';
import { formatNumber } from '@/utils/common.ts';

// ─── Masked value ─────────────────────────────────────────────────────────────

function MaskedValue({ value }: { value: string }) {
	const [visible, setVisible] = useState(false);
	const masked = value.length > 8 ? value.slice(0, 4) + '••••••••' + value.slice(-4) : '••••••••';
	return (
		<span className="flex items-center gap-x-1.5 font-mono text-sm">
			{visible ? value : masked}
			<Button size={'icon-sm'} variant={'ghost'} onClick={() => setVisible((v) => !v)}>
				{visible ? <EyeOff /> : <Eye />}
			</Button>
		</span>
	);
}

// ─── Model Card ───────────────────────────────────────────────────────────────

function ModelCardItem({ model }: { model: ModelCard }) {
	const { t } = useTranslation();
	const ctx = model.context_size ? formatNumber(model.context_size) : null;

	const output = model.output_size ? formatNumber(model.output_size) : null;

	const statusVariant =
		model.status === 'active'
			? 'default'
			: model.status === 'deprecated'
				? 'secondary'
				: 'outline';

	const reasoning = model.input_types.includes('application/x-thinking');

	return (
		<Card className="shadow">
			<CardHeader>
				<CardTitle
					className="text-sm font-semibold leading-tight truncate"
					title={model.name}
				>
					{model.label || model.name}
				</CardTitle>
				{reasoning ? (
					<CardAction>
						<Badge variant={'outline'}>{t('credential.reasoning')}</Badge>
					</CardAction>
				) : null}
			</CardHeader>
			<CardContent className="flex flex-col">
				{model.status !== 'active' && (
					<Badge variant={statusVariant} className="text-xs">
						{model.status}
					</Badge>
				)}

				<div className="flex justify-between items-center text-[14px]">
					<span className="text-muted-foreground">{t('credential.maxContext')}</span>
					<span>{ctx}</span>
				</div>
				<div className="flex justify-between items-center text-[14px]">
					<span className="text-muted-foreground">{t('credential.maxOutput')}</span>
					<span>{output}</span>
				</div>
				<div className="flex justify-between items-center text-[14px]">
					<span className="text-muted-foreground">{t('credential.inputTypes')}</span>
					<InputTypeBadges inputTypes={model.input_types} />
				</div>
				<div className="flex justify-between items-center text-[14px]">
					<span className="text-muted-foreground">{t('credential.outputTypes')}</span>
					<InputTypeBadges inputTypes={model.output_types} />
				</div>
			</CardContent>
		</Card>
	);
}

// ─── TTS Model Card ──────────────────────────────────────────────────────────

function TTSModelCardItem({ model }: { model: TTSModelCard }) {
	const { t } = useTranslation();

	const statusVariant =
		model.status === 'active'
			? 'default'
			: model.status === 'deprecated'
				? 'secondary'
				: 'outline';

	return (
		<Card className="shadow">
			<CardHeader>
				<CardTitle
					className="text-sm font-semibold leading-tight truncate"
					title={model.name}
				>
					{model.label || model.name}
				</CardTitle>
				{model.realtime && (
					<CardAction>
						<Badge variant="outline">Realtime</Badge>
					</CardAction>
				)}
			</CardHeader>
			<CardContent className="flex flex-col">
				{model.status !== 'active' && (
					<Badge variant={statusVariant} className="text-xs">
						{model.status}
					</Badge>
				)}
				<div className="flex justify-between items-center text-[14px]">
					<span className="text-muted-foreground">{t('credential.inputTypes')}</span>
					<InputTypeBadges inputTypes={model.input_types} />
				</div>
				<div className="flex justify-between items-center text-[14px]">
					<span className="text-muted-foreground">{t('credential.outputTypes')}</span>
					<InputTypeBadges inputTypes={model.output_types} />
				</div>
			</CardContent>
		</Card>
	);
}

// ─── Detail panel ─────────────────────────────────────────────────────────────

interface DetailPanelProps {
	credential: CredentialView;
	schema: CredentialSchema | null;
	onEdit: () => void;
	onDelete: () => void;
}

function DetailPanel({ credential, schema, onEdit, onDelete }: DetailPanelProps) {
	const { t } = useTranslation();
	const [models, setModels] = useState<ModelCard[]>([]);
	const [ttsModels, setTtsModels] = useState<TTSModelCard[]>([]);
	const [modelsLoading, setModelsLoading] = useState(false);

	const type = credential.data.type as string | undefined;

	useEffect(() => {
		if (!type) return;
		setModelsLoading(true);
		Promise.all([
			modelApi
				.list(type)
				.then((res) => res.models)
				.catch(() => [] as ModelCard[]),
			ttsModelApi
				.list(type)
				.then((res) => res.models)
				.catch(() => [] as TTSModelCard[]),
		])
			.then(([chatModels, tts]) => {
				setModels(chatModels);
				setTtsModels(tts);
			})
			.finally(() => setModelsLoading(false));
	}, [credential.id, type]);

	// Fields to display: use schema properties order, skip id/type/const fields
	const displayFields = schema
		? Object.entries(schema.properties).filter(
				([key, prop]) => key !== 'id' && key !== 'type' && prop.const === undefined,
			)
		: Object.entries(credential.data)
				.filter(([key]) => key !== 'id' && key !== 'type')
				.map(
					([key]) =>
						[key, { title: key, writeOnly: false }] as [
							string,
							{ title: string; writeOnly: boolean },
						],
				);

	const name = (credential.data.name as string | undefined) ?? credential.id;

	return (
		<div className="flex flex-col gap-y-6 p-6 overflow-y-auto h-full">
			{/* Header */}
			<div className="flex items-start justify-between gap-x-4">
				<div className="flex flex-col gap-y-1">
					<h2 className="text-lg font-semibold">{name}</h2>
					<p className="text-muted-foreground text-sm">{type}</p>
					{!credential.editable && (
						<Badge variant="secondary" title={t('common.readOnlyTooltip')}>
							{t('common.readOnly')}
						</Badge>
					)}
				</div>
				<div className="flex items-center gap-x-2 shrink-0">
					<Button
						size="icon-sm"
						variant="outline"
						onClick={onEdit}
						disabled={!credential.editable}
						tooltip={credential.editable ? undefined : t('common.readOnlyTooltip')}
					>
						<Pencil />
					</Button>
					<Button
						size="icon-sm"
						variant="destructive"
						onClick={onDelete}
						disabled={!credential.editable}
						tooltip={credential.editable ? undefined : t('common.readOnlyTooltip')}
					>
						<Trash2 />
					</Button>
				</div>
			</div>

			{/* Fields */}
			<div className="flex flex-col gap-y-3">
				{displayFields.map(([key, prop]) => {
					const schemaProp = prop as {
						title?: string;
						writeOnly?: boolean;
						format?: string;
					};
					const label = schemaProp.title ?? key.replace(/_/g, ' ');
					const isSecret = schemaProp.writeOnly || schemaProp.format === 'password';
					const val = credential.data[key];
					if (val === undefined || val === null) return null;
					const strVal = String(val);
					return (
						<div key={key} className="flex flex-col gap-y-0.5">
							<span className="text-muted-foreground text-xs uppercase tracking-wide">
								{label}
							</span>
							{isSecret ? (
								<MaskedValue value={strVal} />
							) : (
								<span className="text-sm font-mono break-all">{strVal}</span>
							)}
						</div>
					);
				})}
			</div>

			<Separator />

			{/* Available Models */}
			<div className="flex flex-col gap-y-4">
				<h3 className="text-sm font-semibold">
					{t('credential.availableModels')}({models.length})
				</h3>
				{modelsLoading ? (
					<div className="grid grid-cols-2 xl:grid-cols-3 gap-3">
						{Array.from({ length: 4 }).map((_, i) => (
							<Skeleton key={i} className="h-20 rounded-lg" />
						))}
					</div>
				) : models.length === 0 ? (
					<Empty className="border-none py-6">
						<EmptyHeader>
							<EmptyTitle>{t('credential.noModels')}</EmptyTitle>
							<EmptyDescription>
								{t('credential.noModelsDescription')}
							</EmptyDescription>
						</EmptyHeader>
					</Empty>
				) : (
					<div className="grid grid-cols-2 xl:grid-cols-3 gap-3">
						{models.map((m) => (
							<ModelCardItem key={m.name} model={m} />
						))}
					</div>
				)}
			</div>

			{/* Available TTS Models */}
			{ttsModels.length > 0 && (
				<>
					<Separator />
					<div className="flex flex-col gap-y-4">
						<h3 className="text-sm font-semibold">
							{t('credential.availableTTSModels')}({ttsModels.length})
						</h3>
						<div className="grid grid-cols-2 xl:grid-cols-3 gap-3">
							{ttsModels.map((m) => (
								<TTSModelCardItem key={m.name} model={m} />
							))}
						</div>
					</div>
				</>
			)}
		</div>
	);
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export const CredentialPage = () => {
	const { t } = useTranslation();
	const { credentials, loading, remove, refetch } = useCredentials();
	const [schemas, setSchemas] = useState<CredentialSchema[]>([]);
	const [selectedId, setSelectedId] = useState<string | null>(null);
	const [createOpen, setCreateOpen] = useState(false);
	const [createDefaultType, setCreateDefaultType] = useState<string | undefined>();
	const [editOpen, setEditOpen] = useState(false);
	const [deleteOpen, setDeleteOpen] = useState(false);

	useEffect(() => {
		credentialApi.schemas().then((res) => setSchemas(res.schemas));
	}, []);

	// Auto-select first credential
	useEffect(() => {
		if (!selectedId && credentials.length > 0) {
			setSelectedId(credentials[0].id);
		}
	}, [credentials, selectedId]);

	const selectedCredential = credentials.find((c) => c.id === selectedId) ?? null;
	const selectedSchema = selectedCredential
		? (schemas.find(
				(s) =>
					(s.properties.type?.const as string) ===
					(selectedCredential.data.type as string),
			) ?? null)
		: null;

	// Group credentials by type, then list all schema types (even empty ones)
	const groupedByType: Array<{ type: string; title: string; records: CredentialView[] }> =
		schemas.map((s) => {
			const type = s.properties.type?.const as string;
			return {
				type,
				title: s.title,
				records: credentials.filter((c) => c.data.type === type),
			};
		});

	// Split providers so the user's actual configuration leads, and the
	// (mostly empty) "add a provider" entries don't drown it out.
	const configuredGroups = groupedByType.filter((g) => g.records.length > 0);
	const totalConfigured = configuredGroups.reduce((n, g) => n + g.records.length, 0);

	const handleOpenCreate = useCallback((type?: string) => {
		setCreateDefaultType(type);
		setCreateOpen(true);
	}, []);

	const handleDelete = useCallback(async () => {
		if (!selectedCredential) return;
		await remove(selectedCredential.id);
		setSelectedId(null);
	}, [selectedCredential, remove]);

	return (
		<div className="flex h-full w-full">
			{/* Left sidebar */}
			<Sidebar collapsible="none" className="border-r">
				<SidebarHeader className={'flex flex-col mt-5 gap-y-1'}>
					<div className="text-lg font-semibold">{t('common.credential')}</div>
					<div className="text-muted-foreground text-xs">{t('credential.subtitle')}</div>
				</SidebarHeader>
				{/*<Separator />*/}
				<SidebarContent>
					{loading ? (
						<div className="flex flex-col gap-y-2 p-4">
							{Array.from({ length: 3 }).map((_, i) => (
								<Skeleton key={i} className="h-8 rounded" />
							))}
						</div>
					) : groupedByType.length === 0 ? (
						<Empty className="border-none py-8">
							<EmptyHeader>
								<EmptyTitle>{t('credential.noProviders')}</EmptyTitle>
							</EmptyHeader>
						</Empty>
					) : (
						<>
							{/* Configured credentials lead — this is what the user actually set up. */}
							{configuredGroups.length > 0 && (
								<SidebarGroup>
									<SidebarGroupLabel>
										{t('credential.configured')} ({totalConfigured})
									</SidebarGroupLabel>
									<SidebarGroupContent className="flex flex-col gap-y-4">
										{configuredGroups.map(({ type, title, records }) => (
											<div key={type}>
												<div className="px-2 pb-1.5 text-xs font-semibold text-foreground/70">
													{title}
												</div>
												<SidebarMenu className="pl-4">
													{records.map((rec) => {
														const name =
															(rec.data.name as string | undefined) ??
															rec.id;
														return (
															<SidebarMenuItem key={rec.id}>
																<SidebarMenuButton
																	isActive={selectedId === rec.id}
																	onClick={() =>
																		setSelectedId(rec.id)
																	}
																>
																	<span className="min-w-0 flex-1 truncate">
																		{name}
																	</span>
																	{!rec.editable && (
																		<Badge
																			variant="secondary"
																			className="text-[10px] px-1 py-0"
																			title={t(
																				'common.readOnlyTooltip',
																			)}
																		>
																			{t('common.readOnly')}
																		</Badge>
																	)}
																</SidebarMenuButton>
															</SidebarMenuItem>
														);
													})}
												</SidebarMenu>
											</div>
										))}
									</SidebarGroupContent>
								</SidebarGroup>
							)}

							{/* Add credential — every provider is an entry point (including
							    configured ones, to add more under the same provider). */}
							<SidebarGroup>
								<SidebarGroupLabel>{t('credential.addProvider')}</SidebarGroupLabel>
								<SidebarGroupContent>
									<SidebarMenu>
										{groupedByType.map(({ type, title }) => (
											<SidebarMenuItem key={type}>
												<SidebarMenuButton
													onClick={() => handleOpenCreate(type)}
												>
													<Plus />
													<span className="min-w-0 flex-1 truncate">
														{title}
													</span>
												</SidebarMenuButton>
											</SidebarMenuItem>
										))}
									</SidebarMenu>
								</SidebarGroupContent>
							</SidebarGroup>
						</>
					)}
				</SidebarContent>
			</Sidebar>

			{/* Right detail */}
			<main className="flex-1 min-h-0 overflow-hidden">
				{selectedCredential ? (
					<DetailPanel
						credential={selectedCredential}
						schema={selectedSchema}
						onEdit={() => setEditOpen(true)}
						onDelete={() => setDeleteOpen(true)}
					/>
				) : (
					<div className="flex h-full items-center justify-center">
						<Empty className="border-none">
							<EmptyHeader>
								<EmptyTitle>{t('credential.selectHint')}</EmptyTitle>
								<EmptyDescription>
									{t('credential.selectHintDescription')}
								</EmptyDescription>
							</EmptyHeader>
						</Empty>
					</div>
				)}
			</main>

			{/* Dialogs */}
			<CreateCredentialDialog
				open={createOpen}
				onOpenChange={setCreateOpen}
				defaultType={createDefaultType}
				onCreated={() => refetch()}
			/>
			{selectedCredential && (
				<>
					<EditCredentialDialog
						open={editOpen}
						onOpenChange={setEditOpen}
						credential={selectedCredential}
						onUpdated={() => refetch()}
					/>
					<DeleteDialog
						open={deleteOpen}
						onOpenChange={setDeleteOpen}
						title={t('common.deleteTitle', {
							entity: t('credential.deleteEntity'),
							name:
								(selectedCredential.data.name as string | undefined) ??
								selectedCredential.id,
						})}
						description={t('common.deleteDescription')}
						onConfirm={handleDelete}
					/>
				</>
			)}
		</div>
	);
};
