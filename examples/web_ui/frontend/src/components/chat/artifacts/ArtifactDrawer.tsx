import {
	ArrowLeft,
	Copy,
	Download,
	FileText,
	Loader2,
	MessageSquarePlus,
	Pencil,
	RotateCw,
	Save,
	X,
} from 'lucide-react';
import mime from 'mime';
import Papa from 'papaparse';
import { lazy, Suspense, useEffect, useEffectEvent, useMemo, useRef, useState } from 'react';
import { toast } from 'sonner';
import { defaultRehypePlugins } from 'streamdown';

import {
	BINARY_PREVIEW_LIMIT,
	type FileTarget,
	isOfficeKind,
	previewKind,
	PreviewTooLargeError,
	readPreviewBlob,
	TEXT_PREVIEW_LIMIT,
} from './artifacts';
import { staticHtml } from './htmlPreview';
import { OfficePreview, type OfficePreviewError } from './OfficePreview';
import { type FileEditState, useFileLeaveGuard } from './useFileLeaveGuard';
import { ApiError } from '@/api/client';
import { type FileEditAccess, type TextFileSnapshot, workspaceApi } from '@/api/workspace';
import { Markdown } from '@/components/markdown';
import { Button } from '@/components/ui/button';
import {
	Sheet,
	SheetClose,
	SheetContent,
	SheetDescription,
	SheetFooter,
	SheetHeader,
	SheetTitle,
} from '@/components/ui/sheet';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Textarea } from '@/components/ui/textarea';
import { useTranslation } from '@/i18n/useI18n';
import { copyToClipboard } from '@/utils/common';

type Preview =
	| { status: 'loading' | 'unsupported' | 'large' | 'missing' | 'error' | OfficePreviewError }
	| { status: 'ready'; text?: string; url?: string; officeFile?: Blob };

const ArtifactEditor = lazy(() =>
	import('./ArtifactEditor').catch(() => ({ default: ArtifactEditorFallback })),
);

/** A failed editor chunk must not take the user's draft or chat down. */
function ArtifactEditorFallback({
	value,
	readOnly,
	onChange,
}: {
	value: string;
	readOnly: boolean;
	onChange: (value: string) => void;
}) {
	const { t } = useTranslation();
	return (
		<>
			<p role="alert" className="mb-2 text-sm text-muted-foreground">
				{t('artifacts.editorUnavailable')}
			</p>
			<Textarea
				aria-label={t('artifacts.editor')}
				value={value}
				readOnly={readOnly}
				onChange={(event) => onChange(event.target.value)}
				className="min-h-80 flex-1 resize-none field-sizing-fixed font-mono text-sm"
				spellCheck={false}
			/>
		</>
	);
}

// Do not parse raw HTML into elements that can load external resources.
const artifactRehypePlugins = Object.entries(defaultRehypePlugins)
	.filter(([name]) => name !== 'raw')
	.map(([, plugin]) => plugin);

interface Props {
	artifact: FileTarget;
	agentId: string;
	sessionId: string;
	onClose: () => void;
	onReference: (path: string) => void;
	referenceDisabled?: boolean;
	onBack?: () => void;
	changeVersion?: number;
}

/** Mount a fresh drawer for each selected file and session. */
export function ArtifactDrawer(props: Props) {
	const referencing = useRef(false);
	const guard = useFileLeaveGuard();
	return (
		<Sheet
			open
			onOpenChange={(open) => {
				if (!open && guard.confirmLeave()) props.onClose();
			}}
		>
			<SheetContent
				className="gap-0 data-[side=right]:w-full data-[side=right]:sm:max-w-2xl"
				showCloseButton={false}
				onCloseAutoFocus={(event) => {
					if (referencing.current) event.preventDefault();
				}}
			>
				{guard.navigationGuard}
				<ArtifactPreviewContent
					{...props}
					onEditStateChange={guard.setEditState}
					onReference={(path) => {
						if (!guard.confirmLeave()) return;
						referencing.current = true;
						props.onReference(path);
					}}
				/>
			</SheetContent>
		</Sheet>
	);
}

/** Shared by reply artifacts and the workspace directory browser. */
export function ArtifactPreviewContent({
	artifact,
	agentId,
	sessionId,
	onReference,
	referenceDisabled,
	onBack,
	changeVersion = 0,
	onEditStateChange,
}: Omit<Props, 'onClose'> & { onEditStateChange?: (state: FileEditState) => void }) {
	const { t } = useTranslation();
	const kind = previewKind(artifact.path);
	const [preview, setPreview] = useState<Preview>({ status: 'loading' });
	const [attempt, setAttempt] = useState(0);
	const [downloading, setDownloading] = useState(false);
	const downloadRequest = useRef<AbortController | null>(null);
	const editRequest = useRef<AbortController | null>(null);
	const previewRequest = useRef<AbortController | null>(null);
	const observedVersion = useRef(changeVersion);
	const pendingRefresh = useRef(false);
	const [externalChange, setExternalChange] = useState(false);
	const [snapshot, setSnapshot] = useState<TextFileSnapshot | null>(null);
	const [draft, setDraft] = useState('');
	const [editing, setEditing] = useState(false);
	const [editLoading, setEditLoading] = useState(false);
	const [saving, setSaving] = useState(false);
	const [editError, setEditError] = useState<string | null>(null);
	const [editAccess, setEditAccess] = useState<FileEditAccess | null>(null);
	const [accessError, setAccessError] = useState<string | null>(null);
	const [accessAttempt, setAccessAttempt] = useState(0);
	const normalize = (value: string) => value.replace(/^\uFEFF/, '').replace(/\r\n?/g, '\n');
	const dirty = snapshot !== null && draft !== normalize(snapshot.text);
	const editable = ['markdown', 'html', 'csv', 'text'].includes(kind);
	const reopenRequired = editError === 'editSessionChanged' || editError === 'editPathChanged';
	const reloadRequired =
		externalChange || editError === 'editConflict' || editError === 'saveUnconfirmed';
	const saveDisabled = !dirty || saving || editLoading || reopenRequired || reloadRequired;

	useEffect(() => {
		if (!editable) return;
		const controller = new AbortController();
		setEditAccess(null);
		setAccessError(null);
		void workspaceApi.files
			.editAccess(agentId, sessionId, artifact.path, controller.signal)
			.then((access) => {
				if (!controller.signal.aborted) setEditAccess(access);
			})
			.catch((error: unknown) => {
				if (!controller.signal.aborted) {
					setAccessError(
						error instanceof ApiError && error.code === 'path_resolver_unavailable'
							? 'editResolverUnavailable'
							: 'editAccessFailed',
					);
				}
			});
		return () => controller.abort();
	}, [agentId, sessionId, artifact.path, editable, accessAttempt]);

	useEffect(() => {
		onEditStateChange?.({ dirty, saving });
	}, [dirty, saving, onEditStateChange]);
	useEffect(
		() => () => {
			editRequest.current?.abort();
			onEditStateChange?.({ dirty: false, saving: false });
		},
		[onEditStateChange],
	);

	const loadEditor = async (background = false) => {
		if (saving || editLoading || reopenRequired || !editAccess?.editable) return;
		if (dirty && !window.confirm(t('artifacts.discardConfirm'))) return;
		previewRequest.current?.abort();
		editRequest.current?.abort();
		const controller = new AbortController();
		editRequest.current = controller;
		setEditLoading(true);
		setEditError(null);
		const version = observedVersion.current;
		try {
			const next = await workspaceApi.files.readText(
				agentId,
				sessionId,
				artifact.path,
				controller.signal,
			);
			if (controller.signal.aborted) return;
			setSnapshot(next);
			setDraft(normalize(next.text));
			setPreview({ status: 'ready', text: next.text });
			if (version === observedVersion.current) setExternalChange(false);
			if (!background) setEditing(true);
		} catch (error) {
			if (controller.signal.aborted) return;
			if (background) setExternalChange(true);
			setEditError(
				error instanceof ApiError && error.code === 'mixed_line_endings'
					? 'editLineEndings'
					: error instanceof ApiError && error.status === 403
						? 'editForbidden'
						: error instanceof ApiError && error.status === 415
							? 'editEncoding'
							: error instanceof ApiError && error.status === 413
								? 'editTooLarge'
								: 'editLoadFailed',
			);
		} finally {
			if (!controller.signal.aborted) setEditLoading(false);
		}
	};

	const save = async () => {
		if (!snapshot || saveDisabled) return;
		// The backend rejects mixed endings. Preserve the source's single style.
		const body = snapshot.text.includes('\r\n')
			? draft.replace(/\n/g, '\r\n')
			: snapshot.text.includes('\r')
				? draft.replace(/\n/g, '\r')
				: draft;
		const text = snapshot.text.startsWith('\uFEFF') ? '\uFEFF' + body : body;
		if (new Blob([text]).size > TEXT_PREVIEW_LIMIT) {
			setEditError('editTooLarge');
			return;
		}
		editRequest.current?.abort();
		const controller = new AbortController();
		editRequest.current = controller;
		setSaving(true);
		setEditError(null);
		try {
			const next = await workspaceApi.files.saveText(
				agentId,
				sessionId,
				artifact.path,
				text,
				snapshot,
				controller.signal,
			);
			if (controller.signal.aborted) return;
			setSnapshot(next);
			setDraft(normalize(next.text));
			setPreview({ status: 'ready', text: next.text });
			toast.success(t('artifacts.saved'));
		} catch (error) {
			if (controller.signal.aborted) return;
			if (error instanceof ApiError && error.code === 'save_unconfirmed') {
				setEditError('saveUnconfirmed');
				return;
			}
			setEditError(
				error instanceof ApiError && error.code === 'session_changed'
					? 'editSessionChanged'
					: error instanceof ApiError && error.code === 'path_changed'
						? 'editPathChanged'
						: error instanceof ApiError && error.code === 'file_changed'
							? 'editConflict'
							: error instanceof ApiError && error.status === 403
								? 'editForbidden'
								: 'saveFailed',
			);
		} finally {
			if (!controller.signal.aborted) setSaving(false);
		}
	};

	const refreshAfterChange = useEffectEvent(() => {
		if (dirty || saving || editLoading || reopenRequired) {
			setExternalChange(true);
			// A clean editor may still be reading the previous notification.
			// Drain one more refresh when it finishes, without dropping changes.
			if (editLoading && !dirty && !saving && !reopenRequired) pendingRefresh.current = true;
		} else if (snapshot) {
			if (editAccess?.editable) void loadEditor(true);
			else setExternalChange(true);
		} else {
			setAttempt((value) => value + 1);
		}
	});
	useEffect(() => {
		if (changeVersion === observedVersion.current) return;
		observedVersion.current = changeVersion;
		refreshAfterChange();
	}, [changeVersion]);
	const finishPendingRefresh = useEffectEvent(() => {
		if (!editError) refreshAfterChange();
	});
	useEffect(() => {
		if (editLoading || !pendingRefresh.current) return;
		pendingRefresh.current = false;
		finishPendingRefresh();
	}, [editLoading]);

	const refresh = () => {
		if (snapshot) void loadEditor(true);
		else setAttempt((value) => value + 1);
	};

	useEffect(() => {
		const controller = new AbortController();
		previewRequest.current = controller;
		let objectUrl: string | undefined;
		setPreview({ status: 'loading' });
		if (kind === 'binary') {
			setPreview({ status: 'unsupported' });
			return;
		}
		const load = async () => {
			const response = await workspaceApi.files.read(
				agentId,
				sessionId,
				artifact.path,
				controller.signal,
			);
			const binary = isOfficeKind(kind) || kind === 'image' || kind === 'pdf';
			const blob = await readPreviewBlob(
				response,
				binary ? BINARY_PREVIEW_LIMIT : TEXT_PREVIEW_LIMIT,
			);
			if (controller.signal.aborted) return;
			if (isOfficeKind(kind)) {
				setPreview({ status: 'ready', officeFile: blob });
			} else if (binary) {
				// Force a display MIME from the selected extension; never execute a
				// response's arbitrary Content-Type in the application's origin.
				objectUrl = URL.createObjectURL(
					blob.slice(
						0,
						blob.size,
						mime.getType(artifact.name) ?? 'application/octet-stream',
					),
				);
				setPreview({ status: 'ready', url: objectUrl });
			} else {
				const text = await blob.text();
				if (!controller.signal.aborted) setPreview({ status: 'ready', text });
			}
		};
		void load().catch((error: unknown) => {
			if (controller.signal.aborted) return;
			setPreview({
				status:
					error instanceof PreviewTooLargeError
						? 'large'
						: error instanceof ApiError && error.status === 404
							? 'missing'
							: 'error',
			});
		});
		return () => {
			controller.abort();
			if (objectUrl) URL.revokeObjectURL(objectUrl);
		};
	}, [agentId, sessionId, artifact.path, artifact.name, kind, attempt]);

	useEffect(() => () => downloadRequest.current?.abort(), []);

	const download = async () => {
		downloadRequest.current?.abort();
		const controller = new AbortController();
		downloadRequest.current = controller;
		setDownloading(true);
		try {
			const url = await workspaceApi.files.downloadUrl(
				agentId,
				sessionId,
				artifact.path,
				controller.signal,
			);
			if (controller.signal.aborted) return;
			const link = document.createElement('a');
			link.href = url;
			link.download = artifact.name;
			link.rel = 'noopener noreferrer';
			link.target = '_blank';
			document.body.appendChild(link);
			link.click();
			link.remove();
		} catch {
			if (!controller.signal.aborted) toast.error(t('artifacts.downloadFailed'));
		} finally {
			if (!controller.signal.aborted) setDownloading(false);
		}
	};

	const text = snapshot ? draft : preview.status === 'ready' ? preview.text : undefined;
	return (
		<>
			<SheetHeader className="gap-2 border-b p-5">
				{onBack && (
					<Button variant="ghost" size="sm" className="-ml-2 w-fit" onClick={onBack}>
						<ArrowLeft />
						{t('artifacts.backToFiles')}
					</Button>
				)}
				<div className="flex min-w-0 items-center gap-3">
					<FileText className="size-5 shrink-0 text-muted-foreground" />
					<SheetTitle className="min-w-0 flex-1 truncate" title={artifact.name}>
						{artifact.name}
					</SheetTitle>
					<SheetClose asChild>
						<Button variant="ghost" size="icon-sm" aria-label={t('common.close')}>
							<X />
						</Button>
					</SheetClose>
				</div>
				<SheetDescription className="break-all font-mono text-xs">
					{artifact.path}
				</SheetDescription>
				<p className="text-xs text-muted-foreground">
					{t(dirty ? 'artifacts.draftPreview' : 'artifacts.currentFile')}
				</p>
				{editable && preview.status === 'ready' && (
					<div className="flex flex-wrap items-center gap-2">
						<Button
							variant={editing ? 'outline' : 'secondary'}
							size="sm"
							onClick={() => setEditing(false)}
						>
							{t('artifacts.preview')}
						</Button>
						<Button
							variant={editing ? 'secondary' : 'outline'}
							size="sm"
							disabled={
								editLoading || saving || reopenRequired || !editAccess?.editable
							}
							onClick={() => (snapshot ? setEditing(true) : void loadEditor())}
						>
							{editLoading ? <Loader2 className="animate-spin" /> : <Pencil />}
							{t('artifacts.edit')}
						</Button>
						{snapshot && (
							<Button size="sm" disabled={saveDisabled} onClick={() => void save()}>
								{saving ? <Loader2 className="animate-spin" /> : <Save />}
								{t(saving ? 'artifacts.saving' : 'artifacts.save')}
							</Button>
						)}
						{dirty && (
							<span className="text-xs text-muted-foreground">
								{t('artifacts.unsaved')}
							</span>
						)}
					</div>
				)}
				<div className="flex flex-wrap items-center gap-2">
					<Button
						variant="ghost"
						size="sm"
						disabled={
							saving ||
							editLoading ||
							preview.status === 'loading' ||
							reopenRequired ||
							(snapshot !== null && !editAccess?.editable)
						}
						onClick={refresh}
					>
						<RotateCw className={editLoading ? 'animate-spin' : undefined} />
						{t(reloadRequired ? 'artifacts.reloadFile' : 'artifacts.refresh')}
					</Button>
					{externalChange && (
						<p role="status" className="text-xs text-muted-foreground">
							{t('artifacts.fileUpdated')}
						</p>
					)}
				</div>
				{editable && preview.status === 'ready' && !editAccess?.editable && (
					<p className="text-xs text-muted-foreground" role="status">
						{accessError
							? t(`artifacts.${accessError}`)
							: editAccess === null
								? t('artifacts.editAccessChecking')
								: !editAccess.editable
									? t('artifacts.editOutsideRoots')
									: null}
						{accessError && (
							<Button
								variant="ghost"
								size="sm"
								onClick={() => setAccessAttempt((value) => value + 1)}
							>
								<RotateCw />
								{t('artifacts.retry')}
							</Button>
						)}
					</p>
				)}
				{editError && (
					<div role="alert" className="space-y-2 text-sm text-destructive">
						<p>{t(`artifacts.${editError}`)}</p>
					</div>
				)}
			</SheetHeader>
			<div
				className="flex min-h-0 flex-1 flex-col overflow-auto p-5"
				aria-busy={preview.status === 'loading'}
			>
				{editing && snapshot ? (
					<Suspense fallback={<span role="status">{t('common.loading')}</span>}>
						<ArtifactEditor
							path={artifact.path}
							value={draft}
							onChange={(value) => setDraft(value.replace(/\r\n?/g, '\n'))}
							readOnly={saving || editLoading || reopenRequired}
						/>
					</Suspense>
				) : preview.status === 'loading' ? (
					<div
						role="status"
						className="flex flex-1 items-center justify-center gap-2 text-muted-foreground"
					>
						<Loader2 className="size-4 animate-spin" />
						{t('common.loading')}
					</div>
				) : preview.status === 'ready' && preview.officeFile && isOfficeKind(kind) ? (
					<OfficePreview
						kind={kind}
						file={preview.officeFile}
						name={artifact.name}
						onError={(status) => setPreview({ status })}
					/>
				) : preview.status === 'ready' ? (
					<ArtifactPreview
						kind={kind}
						name={artifact.name}
						text={text}
						url={preview.url}
						onError={() => setPreview({ status: 'error' })}
					/>
				) : (
					<div
						role="status"
						className="flex flex-1 flex-col items-center justify-center gap-4 text-center text-sm text-muted-foreground"
					>
						<FileText className="size-8" />
						<p>{t(`artifacts.${preview.status}`)}</p>
						{(preview.status === 'error' || preview.status === 'missing') && (
							<Button variant="outline" size="sm" onClick={refresh}>
								<RotateCw />
								{t('artifacts.retry')}
							</Button>
						)}
					</div>
				)}
			</div>
			<SheetFooter className="flex-row flex-wrap justify-between gap-2 border-t p-4">
				<Button
					variant="outline"
					disabled={referenceDisabled}
					onClick={() => {
						onReference(artifact.path);
					}}
				>
					<MessageSquarePlus />
					{t('artifacts.reference')}
				</Button>
				<div className="flex gap-2">
					{text !== undefined && (
						<Button
							variant="ghost"
							size="icon"
							aria-label={t('common.copy')}
							onClick={async () => {
								const copied = await copyToClipboard(text);
								if (copied) toast.success(t('artifacts.copied'));
								else toast.error(t('artifacts.copyFailed'));
							}}
						>
							<Copy />
						</Button>
					)}
					<Button
						variant="outline"
						disabled={downloading}
						onClick={() => void download()}
					>
						{downloading ? <Loader2 className="animate-spin" /> : <Download />}
						{t(dirty ? 'artifacts.downloadSaved' : 'artifacts.download')}
					</Button>
				</div>
			</SheetFooter>
		</>
	);
}

function ArtifactPreview({
	kind,
	name,
	text,
	url,
	onError,
}: {
	kind: ReturnType<typeof previewKind>;
	name: string;
	text?: string;
	url?: string;
	onError: () => void;
}) {
	const { t } = useTranslation();
	const html = useMemo(() => (kind === 'html' ? staticHtml(text ?? '') : ''), [kind, text]);
	if (kind === 'image')
		return (
			<img
				src={url}
				alt={name}
				onError={onError}
				className="m-auto max-h-full max-w-full object-contain"
			/>
		);
	if (kind === 'pdf')
		return <iframe src={url} title={name} className="min-h-96 w-full flex-1 border-0" />;
	const source = (
		<pre className="whitespace-pre-wrap break-words font-mono text-xs leading-relaxed">
			{text}
		</pre>
	);
	if (kind === 'text') return source;
	return (
		<Tabs defaultValue="preview" className="min-h-0 flex-1">
			<TabsList>
				<TabsTrigger value="preview">{t('artifacts.preview')}</TabsTrigger>
				<TabsTrigger value="source">{t('artifacts.source')}</TabsTrigger>
			</TabsList>
			<TabsContent value="source" className="overflow-auto pt-3">
				{source}
			</TabsContent>
			<TabsContent value="preview" className="min-h-0 overflow-auto pt-3">
				{kind === 'markdown' && (
					<Markdown
						mode="static"
						rehypePlugins={artifactRehypePlugins}
						components={{ img: ArtifactImagePlaceholder }}
					>
						{text ?? ''}
					</Markdown>
				)}
				{kind === 'csv' && <CsvPreview text={text ?? ''} />}
				{kind === 'html' && (
					<div className="flex h-full min-h-96 flex-col gap-3">
						<p className="text-xs text-muted-foreground">{t('artifacts.staticHtml')}</p>
						<iframe
							srcDoc={html}
							sandbox=""
							referrerPolicy="no-referrer"
							title={name}
							className="min-h-80 w-full flex-1 rounded-md border bg-white"
						/>
					</div>
				)}
			</TabsContent>
		</Tabs>
	);
}

function ArtifactImagePlaceholder(props: object) {
	const { t } = useTranslation();
	const alt = 'alt' in props && typeof props.alt === 'string' ? props.alt : '';
	return (
		<span className="inline-block rounded border px-2 py-1 text-xs text-muted-foreground">
			{t('artifacts.imageBlocked')}
			{alt ? ` (${alt})` : ''}
		</span>
	);
}

function CsvPreview({ text }: { text: string }) {
	const { t } = useTranslation();
	const result = useMemo(
		() => Papa.parse<string[]>(text, { preview: 201, skipEmptyLines: true }),
		[text],
	);
	const truncated =
		result.meta.truncated ||
		result.data.length > 200 ||
		result.data.some((row) => row.length > 40 || row.some((cell) => cell.length > 500));
	return (
		<div className="space-y-3">
			{(truncated || result.errors.length > 0) && (
				<p className="text-xs text-muted-foreground">{t('artifacts.csvLimited')}</p>
			)}
			<div className="overflow-auto rounded-md border">
				<table className="w-full border-collapse text-left text-xs">
					<caption className="sr-only">{t('artifacts.preview')}</caption>
					<tbody>
						{result.data.slice(0, 200).map((row, index) => (
							<tr key={index} className="border-b last:border-0 even:bg-muted/40">
								{row.slice(0, 40).map((cell, column) => (
									<td
										key={column}
										className="max-w-72 border-r px-3 py-2 break-words whitespace-pre-wrap last:border-0"
									>
										{cell.slice(0, 500)}
									</td>
								))}
							</tr>
						))}
					</tbody>
				</table>
			</div>
		</div>
	);
}
