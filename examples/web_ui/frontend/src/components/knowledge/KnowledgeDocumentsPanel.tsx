import { AlertCircle, CheckCircle2, FileText, Loader2, Plus, Trash2, X } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';

import { knowledgeBaseApi } from '@/api';
import type { KnowledgeDocumentStatus, KnowledgeDocumentView } from '@/api';
import { DeleteDialog } from '@/components/dialog/DeleteDialog.tsx';
import { Button } from '@/components/ui/button.tsx';
import {
	Empty,
	EmptyContent,
	EmptyDescription,
	EmptyHeader,
	EmptyMedia,
	EmptyTitle,
} from '@/components/ui/empty.tsx';
import { useUploadContext } from '@/context/UploadContext';
import { isInFlight } from '@/context/uploadTypes';
import type { UploadPhase, UploadTask } from '@/context/uploadTypes';
import { useDocumentStatusPolling } from '@/hooks/useDocumentStatusPolling';
import { useKnowledgeDocuments } from '@/hooks/useKnowledgeDocuments';
import { useKnowledgeSupportedContentTypes } from '@/hooks/useKnowledgeSupportedContentTypes';
import { cn } from '@/lib/utils';

interface KnowledgeDocumentsPanelProps {
	knowledgeBaseId: string;
}

/**
 * A row that the panel renders. Either a server-side document (the
 * canonical record after upload returns) or a still-uploading local
 * task. Uploads that have already produced a `documentId` are routed
 * through the server row so the panel never double-renders one.
 */
type Row =
	| { kind: 'server'; doc: KnowledgeDocumentView; localTask: UploadTask | null }
	| { kind: 'local'; task: UploadTask };

const TERMINAL_SERVER_STATUSES: KnowledgeDocumentStatus[] = ['ready', 'error'];

function formatSize(bytes: number): string {
	if (!bytes) return '—';
	const units = ['B', 'KB', 'MB', 'GB'];
	let value = bytes;
	let i = 0;
	while (value >= 1024 && i < units.length - 1) {
		value /= 1024;
		i++;
	}
	return `${value.toFixed(value >= 100 || i === 0 ? 0 : 1)} ${units[i]}`;
}

function StatusBadge({ phase }: { phase: UploadPhase }) {
	const { t } = useTranslation();
	const label = t(`knowledge.document.status.${phase}`);
	const tone = (() => {
		switch (phase) {
			case 'ready':
				return 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-400';
			case 'error':
				return 'bg-destructive/10 text-destructive';
			case 'cancelled':
				return 'bg-muted text-muted-foreground';
			case 'queued':
			case 'uploading':
			case 'pending':
			case 'parsing':
			case 'chunking':
			case 'indexing':
			default:
				return 'bg-primary/10 text-primary';
		}
	})();
	return (
		<span
			className={cn(
				'inline-flex items-center gap-x-1 rounded-md px-1.5 py-0.5 text-[10px] font-medium',
				tone,
			)}
		>
			{phase === 'ready' ? (
				<CheckCircle2 className="size-3" />
			) : phase === 'error' ? (
				<AlertCircle className="size-3" />
			) : phase === 'cancelled' ? null : (
				<Loader2 className="size-3 animate-spin" />
			)}
			{label}
		</span>
	);
}

interface RowViewProps {
	row: Row;
	onCancel: (taskId: string) => void;
	onDismiss: (taskId: string) => void;
	onDelete: (doc: KnowledgeDocumentView) => void;
}

function RowView({ row, onCancel, onDismiss, onDelete }: RowViewProps) {
	const { t } = useTranslation();

	const filename = row.kind === 'server' ? row.doc.filename : row.task.filename;
	const size = row.kind === 'server' ? row.doc.size : row.task.size;

	// Phase resolution — the local task wins while the server hasn't
	// caught up; once the server says `ready` / `error`, that's the
	// truth.
	const phase: UploadPhase = (() => {
		if (row.kind === 'local') return row.task.phase;
		const localPhase = row.localTask?.phase;
		if (
			localPhase &&
			!TERMINAL_SERVER_STATUSES.includes(row.doc.status as KnowledgeDocumentStatus)
		) {
			return localPhase;
		}
		return row.doc.status;
	})();

	// Progress bar logic: byte-level during `uploading`; otherwise a
	// coarse phase-derived value so the user gets visible movement.
	const { progressValue, indeterminate } = (() => {
		if (phase === 'uploading' && row.kind === 'local') {
			if (!row.task.size) return { progressValue: 0, indeterminate: true };
			return {
				progressValue: Math.min(100, Math.round((row.task.loaded / row.task.size) * 100)),
				indeterminate: false,
			};
		}
		switch (phase) {
			case 'queued':
				return { progressValue: 5, indeterminate: false };
			case 'pending':
				return { progressValue: 35, indeterminate: true };
			case 'parsing':
				return { progressValue: 55, indeterminate: true };
			case 'chunking':
				return { progressValue: 75, indeterminate: true };
			case 'indexing':
				return { progressValue: 90, indeterminate: true };
			case 'ready':
				return { progressValue: 100, indeterminate: false };
			default:
				return { progressValue: 0, indeterminate: false };
		}
	})();

	const error = row.kind === 'local' ? row.task.error : row.doc.error;
	const chunkCount = row.kind === 'server' ? row.doc.chunk_count : row.task.documentId ? 0 : 0;

	const taskIdForCancel =
		row.kind === 'local' ? row.task.taskId : (row.localTask?.taskId ?? null);

	const showProgress = phase !== 'ready' && phase !== 'cancelled';

	return (
		<div className="border-border bg-card flex flex-col gap-y-2 rounded-lg border p-3">
			<div className="flex items-start gap-x-3">
				<FileText className="text-muted-foreground mt-0.5 size-4 shrink-0" />
				<div className="flex min-w-0 flex-1 flex-col gap-y-0.5">
					<div className="flex items-center gap-x-2">
						<span className="truncate text-sm font-medium">{filename}</span>
						<StatusBadge phase={phase} />
					</div>
					<div className="text-muted-foreground flex items-center gap-x-2 text-xs">
						<span>{formatSize(size)}</span>
						{chunkCount > 0 && (
							<>
								<span>·</span>
								<span>
									{t('knowledge.document.chunkCount', {
										count: chunkCount,
									})}
								</span>
							</>
						)}
					</div>
				</div>
				<div className="flex shrink-0 items-center gap-x-1">
					{phase === 'queued' || phase === 'uploading' ? (
						<Button
							variant="ghost"
							size="icon-xs"
							onClick={() => taskIdForCancel && onCancel(taskIdForCancel)}
							aria-label={t('knowledge.document.actions.cancel')}
						>
							<X className="size-3.5" />
						</Button>
					) : phase === 'cancelled' || phase === 'error' ? (
						row.kind === 'local' ? (
							<Button
								variant="ghost"
								size="icon-xs"
								onClick={() => onDismiss(row.task.taskId)}
								aria-label={t('knowledge.document.actions.dismiss')}
							>
								<X className="size-3.5" />
							</Button>
						) : (
							<Button
								variant="ghost"
								size="icon-xs"
								onClick={() => onDelete(row.doc)}
								aria-label={t('knowledge.document.actions.remove')}
							>
								<Trash2 className="size-3.5" />
							</Button>
						)
					) : row.kind === 'server' ? (
						<Button
							variant="ghost"
							size="icon-xs"
							onClick={() => onDelete(row.doc)}
							aria-label={t('knowledge.document.actions.remove')}
						>
							<Trash2 className="size-3.5" />
						</Button>
					) : null}
				</div>
			</div>
			{showProgress && (
				<div className="bg-muted relative h-1 w-full overflow-hidden rounded-full">
					<div
						className={cn(
							'h-full rounded-full transition-all',
							phase === 'error' ? 'bg-destructive' : 'bg-primary',
							indeterminate && 'animate-pulse',
						)}
						style={{ width: `${progressValue}%` }}
					/>
				</div>
			)}
			{phase === 'error' && error && <p className="text-destructive text-xs">{error}</p>}
		</div>
	);
}

export function KnowledgeDocumentsPanel({ knowledgeBaseId }: KnowledgeDocumentsPanelProps) {
	const { t } = useTranslation();
	const fileInputRef = useRef<HTMLInputElement>(null);
	const [dragOver, setDragOver] = useState(false);
	const [deleteTarget, setDeleteTarget] = useState<KnowledgeDocumentView | null>(null);

	const { enqueue, cancel, dismiss, tasksForKb, clearFinishedForKb } = useUploadContext();
	const { documents, refetch } = useKnowledgeDocuments(knowledgeBaseId);
	const tasks = tasksForKb(knowledgeBaseId);
	const { statuses } = useDocumentStatusPolling({
		knowledgeBaseId,
		documents,
	});
	const { mediaTypes, extensions } = useKnowledgeSupportedContentTypes();

	// Lowercased extension set used to gate `handleFiles`. Built once
	// per render — the underlying list is shared across the app via a
	// module-level cache so this stays cheap.
	const extensionSet = useMemo(
		() => new Set(extensions.map((ext) => ext.toLowerCase())),
		[extensions],
	);
	// `<input accept>` takes a comma-separated string of MIME types and
	// `.ext` tokens. We hand it both because browsers diverge on which
	// they honour for any given filename — extensions filter the picker
	// dialog, MIME types catch programmatic drops.
	const acceptAttr = useMemo(
		() => [...extensions, ...mediaTypes].join(','),
		[extensions, mediaTypes],
	);

	// Refetch the canonical list whenever a polled doc reaches a
	// terminal state — this is the single hook that pulls in the worker's
	// final `chunk_count` and any updates to `error`.
	const terminalIdsKey = useMemo(() => {
		const ids: string[] = [];
		for (const id of Object.keys(statuses)) {
			const s = statuses[id];
			if (s.status === 'ready' || s.status === 'error') ids.push(id);
		}
		return ids.join(',');
	}, [statuses]);
	const lastRefetchedKey = useRef('');
	useEffect(() => {
		if (!terminalIdsKey) return;
		if (terminalIdsKey === lastRefetchedKey.current) return;
		lastRefetchedKey.current = terminalIdsKey;
		void refetch();
	}, [terminalIdsKey, refetch]);

	// Merge tasks + documents into a single ordered row list.
	const rows: Row[] = useMemo(() => {
		const tasksByDocId = new Map<string, UploadTask>();
		const tasksWithoutDoc: UploadTask[] = [];
		for (const task of tasks) {
			if (task.documentId) tasksByDocId.set(task.documentId, task);
			else tasksWithoutDoc.push(task);
		}
		const docRows: Row[] = documents.map((doc) => ({
			kind: 'server',
			doc,
			localTask: tasksByDocId.get(doc.id) ?? null,
		}));
		const localRows: Row[] = tasksWithoutDoc.map((task) => ({
			kind: 'local',
			task,
		}));
		// Local-only rows go first (they're either uploading or queued
		// — both are user-visible "I just hit upload" states).
		return [...localRows, ...docRows];
	}, [documents, tasks]);

	const onUploadClick = useCallback(() => {
		fileInputRef.current?.click();
	}, []);

	const handleFiles = useCallback(
		(files: FileList | File[]) => {
			const arr = Array.from(files);
			if (arr.length === 0) return;
			// When the capability list hasn't loaded yet, allow everything
			// through — the backend still rejects unsupported uploads.
			// Once we have the list, filter client-side by extension
			// (`file.type` is unreliable across browsers/OSes).
			const accepted: File[] = [];
			for (const f of arr) {
				if (extensionSet.size === 0) {
					accepted.push(f);
					continue;
				}
				const dot = f.name.lastIndexOf('.');
				const ext = dot >= 0 ? f.name.slice(dot).toLowerCase() : '';
				if (ext && extensionSet.has(ext)) {
					accepted.push(f);
				} else {
					toast.error(
						t('knowledge.document.unsupportedExtension', {
							name: f.name,
						}),
					);
				}
			}
			if (accepted.length === 0) return;
			enqueue(knowledgeBaseId, accepted);
		},
		[enqueue, knowledgeBaseId, extensionSet, t],
	);

	const onFileInputChange = useCallback(
		(e: React.ChangeEvent<HTMLInputElement>) => {
			if (e.target.files) handleFiles(e.target.files);
			// Reset so picking the same file twice re-fires the change.
			e.target.value = '';
		},
		[handleFiles],
	);

	const onDrop = useCallback(
		(e: React.DragEvent<HTMLDivElement>) => {
			e.preventDefault();
			setDragOver(false);
			if (e.dataTransfer.files) handleFiles(e.dataTransfer.files);
		},
		[handleFiles],
	);

	const onDragOver = useCallback((e: React.DragEvent<HTMLDivElement>) => {
		e.preventDefault();
		setDragOver(true);
	}, []);

	const onDragLeave = useCallback(() => setDragOver(false), []);

	const handleDelete = useCallback(async () => {
		if (!deleteTarget) return;
		try {
			await knowledgeBaseApi.deleteDocument(knowledgeBaseId, deleteTarget.id);
			await refetch();
		} catch {
			toast.error(t('knowledge.document.deleteError'));
		}
	}, [deleteTarget, knowledgeBaseId, refetch, t]);

	const hasFinishedLocalTasks = tasks.some((t) => !isInFlight(t.phase));

	const totalCount = rows.length;

	return (
		<div className="flex flex-col gap-y-4">
			<div className="flex items-center justify-between">
				<h3 className="text-sm font-semibold">
					{t('knowledge.document.countLabel', { count: totalCount })}
				</h3>
				<div className="flex items-center gap-x-2">
					{hasFinishedLocalTasks && (
						<Button
							variant="ghost"
							size="sm"
							onClick={() => clearFinishedForKb(knowledgeBaseId)}
						>
							{t('knowledge.document.actions.clearFinished')}
						</Button>
					)}
					<Button variant="outline" size="sm" onClick={onUploadClick}>
						<Plus className="size-3.5" />
						{t('knowledge.document.uploadButtonShort')}
					</Button>
				</div>
			</div>

			<input
				ref={fileInputRef}
				type="file"
				multiple
				accept={acceptAttr || undefined}
				className="hidden"
				onChange={onFileInputChange}
			/>

			<div
				onDrop={onDrop}
				onDragOver={onDragOver}
				onDragLeave={onDragLeave}
				className={cn(
					'rounded-lg border border-dashed transition-colors',
					dragOver ? 'border-primary bg-primary/5' : 'border-border bg-transparent',
				)}
			>
				{rows.length === 0 ? (
					<Empty className="border-none py-6">
						<EmptyHeader>
							<EmptyMedia variant="icon">
								<FileText />
							</EmptyMedia>
							<EmptyTitle>{t('knowledge.document.emptyTitle')}</EmptyTitle>
							<EmptyDescription>
								{t('knowledge.document.emptyDescription')}
							</EmptyDescription>
						</EmptyHeader>
						<EmptyContent>
							<Button variant="outline" size="sm" onClick={onUploadClick}>
								<Plus className="size-3.5" />
								{t('knowledge.document.uploadButton')}
							</Button>
							<p className="text-muted-foreground mt-2 text-xs">
								{t('knowledge.document.dropHintIdle')}
							</p>
						</EmptyContent>
					</Empty>
				) : (
					<div className="flex flex-col gap-y-2 p-2">
						{rows.map((row) => (
							<RowView
								key={
									row.kind === 'server'
										? `srv-${row.doc.id}`
										: `tsk-${row.task.taskId}`
								}
								row={row}
								onCancel={cancel}
								onDismiss={dismiss}
								onDelete={setDeleteTarget}
							/>
						))}
					</div>
				)}
			</div>

			<DeleteDialog
				open={deleteTarget !== null}
				onOpenChange={(open) => {
					if (!open) setDeleteTarget(null);
				}}
				title={t('knowledge.document.deleteConfirmTitle')}
				description={t('knowledge.document.deleteConfirmDescription', {
					name: deleteTarget?.filename ?? '',
				})}
				onConfirm={handleDelete}
			/>
		</div>
	);
}
