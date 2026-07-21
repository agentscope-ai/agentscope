import {
	AlertTriangle,
	ChevronDown,
	ChevronRight,
	Download,
	File,
	FileSearch,
	Folder,
	FolderOpen,
	LoaderCircle,
	RefreshCw,
} from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';

import { artifactApi, type ArtifactEntry } from '@/api';
import { PanelEmpty } from '@/components/panel/PanelEmpty';
import { Button } from '@/components/ui/button';
import { useTranslation } from '@/i18n/useI18n';

interface ArtifactPanelProps {
	agentId: string | null;
	sessionId: string | null;
}

interface ArtifactPreview {
	entry: ArtifactEntry;
	mediaType: string;
	url: string;
	text: string | null;
}

const ROOT_PATH = '.';

function isTextMediaType(mediaType: string): boolean {
	return (
		mediaType.startsWith('text/') ||
		/(json|javascript|xml|yaml|toml|x-python|x-shellscript)/.test(mediaType)
	);
}

export function ArtifactPanel({ agentId, sessionId }: ArtifactPanelProps) {
	const { t } = useTranslation();
	const [directories, setDirectories] = useState<Record<string, ArtifactEntry[]>>({});
	const [expanded, setExpanded] = useState<Set<string>>(new Set());
	const [loadingDirectories, setLoadingDirectories] = useState<Set<string>>(new Set());
	const [selectedPath, setSelectedPath] = useState<string | null>(null);
	const [preview, setPreview] = useState<ArtifactPreview | null>(null);
	const [previewLoading, setPreviewLoading] = useState(false);
	const [error, setError] = useState<string | null>(null);
	const previewRequest = useRef(0);
	const previewUrl = useRef<string | null>(null);

	const clearPreviewUrl = useCallback(() => {
		if (previewUrl.current) URL.revokeObjectURL(previewUrl.current);
		previewUrl.current = null;
	}, []);

	const loadDirectory = useCallback(
		async (path: string) => {
			if (!agentId || !sessionId) return;
			setLoadingDirectories((current) => new Set(current).add(path));
			setError(null);
			try {
				const entries = await artifactApi.list(agentId, sessionId, path);
				setDirectories((current) => ({ ...current, [path]: entries }));
			} catch (reason) {
				setError(reason instanceof Error ? reason.message : String(reason));
			} finally {
				setLoadingDirectories((current) => {
					const next = new Set(current);
					next.delete(path);
					return next;
				});
			}
		},
		[agentId, sessionId],
	);

	useEffect(() => {
		previewRequest.current += 1;
		clearPreviewUrl();
		setDirectories({});
		setExpanded(new Set());
		setSelectedPath(null);
		setPreview(null);
		setError(null);
		if (agentId && sessionId) void loadDirectory(ROOT_PATH);
	}, [agentId, sessionId, clearPreviewUrl, loadDirectory]);

	useEffect(() => clearPreviewUrl, [clearPreviewUrl]);

	const toggleDirectory = useCallback(
		(entry: ArtifactEntry) => {
			setExpanded((current) => {
				const next = new Set(current);
				if (next.has(entry.path)) next.delete(entry.path);
				else next.add(entry.path);
				return next;
			});
			if (!directories[entry.path]) void loadDirectory(entry.path);
		},
		[directories, loadDirectory],
	);

	const selectFile = useCallback(
		async (entry: ArtifactEntry) => {
			if (!agentId || !sessionId) return;
			const requestId = ++previewRequest.current;
			setSelectedPath(entry.path);
			setPreviewLoading(true);
			setError(null);
			try {
				const response = await artifactApi.content(agentId, sessionId, entry.path);
				const blob = await response.blob();
				if (requestId !== previewRequest.current) return;
				clearPreviewUrl();
				const url = URL.createObjectURL(blob);
				previewUrl.current = url;
				const mediaType = blob.type || entry.media_type || 'application/octet-stream';
				const text = isTextMediaType(mediaType) ? await blob.text() : null;
				if (requestId !== previewRequest.current) {
					URL.revokeObjectURL(url);
					return;
				}
				setPreview({ entry, mediaType, url, text });
			} catch (reason) {
				if (requestId === previewRequest.current) {
					setPreview(null);
					setError(reason instanceof Error ? reason.message : String(reason));
				}
			} finally {
				if (requestId === previewRequest.current) setPreviewLoading(false);
			}
		},
		[agentId, sessionId, clearPreviewUrl],
	);

	const downloadPreview = useCallback(() => {
		if (!preview) return;
		const anchor = document.createElement('a');
		anchor.href = preview.url;
		anchor.download = preview.entry.name;
		anchor.click();
	}, [preview]);

	const refresh = useCallback(() => {
		setDirectories({});
		setExpanded(new Set());
		if (agentId && sessionId) void loadDirectory(ROOT_PATH);
	}, [agentId, sessionId, loadDirectory]);

	const renderEntries = (entries: ArtifactEntry[], depth = 0) =>
		entries.map((entry) => {
			const isExpanded = expanded.has(entry.path);
			const isLoading = loadingDirectories.has(entry.path);
			return (
				<div key={entry.path}>
					<button
						type="button"
						className="flex h-8 w-full items-center gap-1.5 rounded-sm pr-2 text-left text-sm hover:bg-accent data-[selected=true]:bg-accent"
						style={{ paddingLeft: `${depth * 16 + 4}px` }}
						data-selected={!entry.is_directory && selectedPath === entry.path}
						aria-expanded={entry.is_directory ? isExpanded : undefined}
						onClick={() =>
							entry.is_directory ? toggleDirectory(entry) : void selectFile(entry)
						}
					>
						{entry.is_directory ? (
							isLoading ? (
								<LoaderCircle className="size-3.5 shrink-0 animate-spin" />
							) : isExpanded ? (
								<ChevronDown className="size-3.5 shrink-0" />
							) : (
								<ChevronRight className="size-3.5 shrink-0" />
							)
						) : (
							<span className="size-3.5 shrink-0" />
						)}
						{entry.is_directory ? (
							isExpanded ? (
								<FolderOpen className="size-4 shrink-0 text-amber-600" />
							) : (
								<Folder className="size-4 shrink-0 text-amber-600" />
							)
						) : (
							<File className="size-4 shrink-0 text-muted-foreground" />
						)}
						<span className="truncate">{entry.name}</span>
					</button>
					{entry.is_directory && isExpanded && directories[entry.path]
						? renderEntries(directories[entry.path], depth + 1)
						: null}
				</div>
			);
		});

	const renderPreview = () => {
		if (previewLoading) {
			return <LoaderCircle className="size-5 animate-spin text-muted-foreground" />;
		}
		if (error) {
			return (
				<PanelEmpty
					icon={AlertTriangle}
					title={t('panel.artifact.errorTitle')}
					description={error}
				/>
			);
		}
		if (!preview) {
			return (
				<PanelEmpty
					icon={FileSearch}
					title={t('panel.artifact.emptyPreviewTitle')}
					description={t('panel.artifact.emptyPreviewDescription')}
				/>
			);
		}
		if (preview.text !== null) {
			return (
				<pre className="size-full overflow-auto whitespace-pre-wrap break-words p-3 font-mono text-xs">
					{preview.text}
				</pre>
			);
		}
		if (preview.mediaType.startsWith('image/')) {
			return (
				<img
					src={preview.url}
					alt={preview.entry.name}
					className="max-h-full max-w-full object-contain"
				/>
			);
		}
		if (preview.mediaType.startsWith('audio/')) {
			return <audio controls src={preview.url} className="w-full" />;
		}
		if (preview.mediaType.startsWith('video/')) {
			return <video controls src={preview.url} className="max-h-full max-w-full" />;
		}
		if (preview.mediaType === 'application/pdf') {
			return (
				<iframe
					title={preview.entry.name}
					src={preview.url}
					className="size-full border-0"
				/>
			);
		}
		return (
			<div className="flex flex-col items-center gap-3 text-muted-foreground">
				<File className="size-8" />
				<span className="text-xs">{preview.mediaType}</span>
				<Button variant="outline" size="sm" onClick={downloadPreview}>
					<Download />
					{t('panel.artifact.download')}
				</Button>
			</div>
		);
	};

	const rootLoading = loadingDirectories.has(ROOT_PATH);
	const rootEntries = directories[ROOT_PATH] ?? [];

	return (
		<div className="grid min-h-0 flex-1 grid-rows-[minmax(10rem,40%)_minmax(0,1fr)]">
			<section className="flex min-h-0 flex-col border-b pb-2">
				<div className="flex h-8 shrink-0 items-center justify-between">
					<span className="truncate text-xs text-muted-foreground">
						{t('panel.artifact.workspaceFiles')}
					</span>
					<Button
						variant="ghost"
						size="icon-sm"
						title={t('panel.artifact.refresh')}
						disabled={!agentId || !sessionId || rootLoading}
						onClick={refresh}
					>
						<RefreshCw className={rootLoading ? 'animate-spin' : undefined} />
					</Button>
				</div>
				<div className="min-h-0 flex-1 overflow-auto">
					{rootLoading && rootEntries.length === 0 ? (
						<div className="flex size-full items-center justify-center">
							<LoaderCircle className="size-5 animate-spin text-muted-foreground" />
						</div>
					) : !agentId || !sessionId ? (
						<PanelEmpty
							icon={Folder}
							title={t('panel.artifact.noSessionTitle')}
							description={t('panel.artifact.noSessionDescription')}
						/>
					) : rootEntries.length === 0 ? (
						<PanelEmpty
							icon={Folder}
							title={t('panel.artifact.emptyTitle')}
							description={t('panel.artifact.emptyDescription')}
						/>
					) : (
						renderEntries(rootEntries)
					)}
				</div>
			</section>
			<section className="flex min-h-0 flex-col pt-2">
				<div className="flex h-8 shrink-0 items-center justify-between gap-2 border-b px-1">
					<span className="truncate text-xs font-medium">
						{preview?.entry.path ?? t('panel.artifact.preview')}
					</span>
					{preview ? (
						<Button
							variant="ghost"
							size="icon-sm"
							title={t('panel.artifact.download')}
							onClick={downloadPreview}
						>
							<Download />
						</Button>
					) : null}
				</div>
				<div className="flex min-h-0 flex-1 items-center justify-center overflow-hidden">
					{renderPreview()}
				</div>
			</section>
		</div>
	);
}
