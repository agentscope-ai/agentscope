import {
	CornerLeftUp,
	Download,
	File,
	Folder,
	FolderOpen,
	Loader2,
	RefreshCw,
	X,
} from 'lucide-react';
import mime from 'mime';
import { useCallback, useEffect, useRef, useState } from 'react';

import { isOfficeDocument, OfficeFilePreview, triggerFileDownload } from './OfficeFilePreview';
import { workspaceApi } from '@/api';
import type { DirectoryEntry as WorkspaceDirectoryEntry } from '@/api';
import { Button } from '@/components/ui/button';
import {
	Drawer,
	DrawerClose,
	DrawerContent,
	DrawerDescription,
	DrawerHeader,
	DrawerTitle,
	DrawerTrigger,
} from '@/components/ui/drawer';
import {
	Empty,
	EmptyDescription,
	EmptyHeader,
	EmptyMedia,
	EmptyTitle,
} from '@/components/ui/empty';
import { Spinner } from '@/components/ui/spinner';
import { useTranslation } from '@/i18n/useI18n';
import { formatApiErrorForAlert } from '@/lib/api-error';
import { cn } from '@/lib/utils';

interface WorkspaceFilesDrawerProps {
	agentId: string | null;
	sessionId: string | null;
	refreshVersion: number;
}

interface SelectedFile {
	name: string;
	path: string;
}

function childPath(parent: string, name: string): string {
	const base = parent.replace(/\/$/, '');
	return `${base}/${name}`;
}

function parentPath(path: string, root: string): string {
	if (!root) return '';
	const parent = path.slice(0, path.lastIndexOf('/')) || '/';
	return parent === root || parent.startsWith(`${root}/`) ? parent : root;
}

function fileSize(bytes: number | null): string {
	if (bytes == null) return '';
	if (bytes < 1024) return `${bytes} B`;
	const units = ['KB', 'MB', 'GB', 'TB'];
	let size = bytes / 1024;
	let unit = 0;
	while (size >= 1024 && unit < units.length - 1) {
		size /= 1024;
		unit += 1;
	}
	return `${size.toFixed(size >= 10 ? 0 : 1)} ${units[unit]}`;
}

export function WorkspaceFilesDrawer({
	agentId,
	sessionId,
	refreshVersion,
}: WorkspaceFilesDrawerProps) {
	const { t } = useTranslation();
	const [open, setOpen] = useState(false);
	const [path, setPath] = useState('');
	const pathRef = useRef('');
	const [rootPath, setRootPath] = useState<string | null>(null);
	const [entries, setEntries] = useState<WorkspaceDirectoryEntry[]>([]);
	const [selectedFile, setSelectedFile] = useState<SelectedFile | null>(null);
	const [initialLoading, setInitialLoading] = useState(true);
	const [refreshing, setRefreshing] = useState(false);
	const [error, setError] = useState<string | null>(null);
	const [downloadPath, setDownloadPath] = useState<string | null>(null);
	const requestId = useRef(0);
	const inFlight = useRef(0);
	const hasLoaded = useRef(false);

	const load = useCallback(
		async (target: string) => {
			if (!agentId || !sessionId) return;
			const id = ++requestId.current;
			inFlight.current += 1;
			if (hasLoaded.current) setRefreshing(true);
			else setInitialLoading(true);
			setError(null);
			try {
				const listing = await workspaceApi.directories(agentId, sessionId, target, {
					silent: true,
				});
				if (id !== requestId.current) return;
				setEntries(listing.entries);
				pathRef.current = listing.path;
				setPath(listing.path);
				if (target === '') setRootPath(listing.path);
				hasLoaded.current = true;
			} catch (e) {
				if (id === requestId.current) setError(formatApiErrorForAlert(e));
			} finally {
				inFlight.current -= 1;
				if (id === requestId.current) {
					setInitialLoading(false);
					setRefreshing(false);
				}
			}
		},
		[agentId, sessionId],
	);

	useEffect(() => {
		if (!open) return;
		const target = '';
		setEntries([]);
		setSelectedFile(null);
		setPath(target);
		pathRef.current = target;
		setRootPath(null);
		hasLoaded.current = false;
		void load(target);
	}, [open, load]);

	useEffect(() => {
		if (open && hasLoaded.current) void load(pathRef.current);
	}, [refreshVersion, open, load]);

	useEffect(() => {
		if (!open) return;
		const timer = window.setInterval(() => {
			if (inFlight.current === 0) void load(pathRef.current);
		}, 5000);
		return () => window.clearInterval(timer);
	}, [open, load]);

	const sortedEntries = [...entries].sort((left, right) => {
		if (left.is_dir !== right.is_dir) return left.is_dir ? -1 : 1;
		return left.name.localeCompare(right.name);
	});
	const selectedEntry = selectedFile
		? entries.find(
				(entry) => !entry.is_dir && childPath(path, entry.name) === selectedFile.path,
			)
		: undefined;
	const atRoot = rootPath === null || path === rootPath;

	const download = async (filePath: string, name: string) => {
		if (!agentId || !sessionId) return;
		setDownloadPath(filePath);
		setError(null);
		try {
			const { token } = await workspaceApi.downloadToken(agentId, sessionId, filePath);
			triggerFileDownload(
				workspaceApi.downloadUrl(agentId, sessionId, filePath, token),
				name,
			);
		} catch (e) {
			setError(formatApiErrorForAlert(e));
		} finally {
			setDownloadPath(null);
		}
	};
	const openFile = async (file: SelectedFile) => {
		if (!agentId || !sessionId) throw new Error(t('workspaceFiles.noSession'));
		const blob = await workspaceApi.readFile(agentId, sessionId, file.path);
		return new globalThis.File([blob], file.name, {
			type: mime.getType(file.name) || blob.type || 'application/octet-stream',
		});
	};

	return (
		<Drawer open={open} onOpenChange={setOpen} direction="right">
			<DrawerTrigger asChild>
				<Button
					variant="secondary"
					size="sm"
					disabled={!agentId || !sessionId}
					className="shrink-0"
					title={t('workspaceFiles.title')}
					aria-label={t('workspaceFiles.title')}
				>
					<FolderOpen />
					<span className="hidden sm:inline">{t('workspaceFiles.title')}</span>
				</Button>
			</DrawerTrigger>
			<DrawerContent className="h-full max-h-full data-[vaul-drawer-direction=right]:sm:max-w-2xl">
				<DrawerHeader className="shrink-0">
					<div className="flex items-start justify-between gap-3">
						<div className="min-w-0">
							<DrawerTitle>{t('workspaceFiles.title')}</DrawerTitle>
							<DrawerDescription className="mt-1 break-all font-mono text-xs">
								{path || t('workspaceFiles.root')}
							</DrawerDescription>
						</div>
						<div className="flex shrink-0 items-center gap-1">
							<Button
								variant="ghost"
								size="icon"
								title={t('workspaceFiles.refresh')}
								aria-label={t('workspaceFiles.refresh')}
								disabled={refreshing || initialLoading}
								onClick={() => void load(pathRef.current)}
							>
								{refreshing ? <Loader2 className="animate-spin" /> : <RefreshCw />}
							</Button>
							<DrawerClose asChild>
								<Button variant="ghost" size="icon" aria-label={t('common.close')}>
									<X />
								</Button>
							</DrawerClose>
						</div>
					</div>
				</DrawerHeader>
				<div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto px-4 pb-4">
					{error && (
						<div className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">
							{error}
						</div>
					)}
					<div className="min-h-32 flex-1 overflow-y-auto rounded-md border p-1">
						{initialLoading ? (
							<div className="flex h-full items-center justify-center">
								<Spinner className="text-muted-foreground" />
							</div>
						) : entries.length === 0 ? (
							<div>
								<Button
									variant="ghost"
									size="sm"
									className="justify-start font-normal"
									disabled={atRoot}
									onClick={() => {
										setSelectedFile(null);
										void load(parentPath(path, rootPath ?? path));
									}}
								>
									<CornerLeftUp />
									{t('workspaceFiles.parent')}
								</Button>
								<Empty className="min-h-32 border-0 p-4">
									<EmptyHeader>
										<EmptyMedia variant="icon">
											<Folder />
										</EmptyMedia>
										<EmptyTitle>{t('workspaceFiles.empty')}</EmptyTitle>
										<EmptyDescription>
											{t('workspaceFiles.emptyDescription')}
										</EmptyDescription>
									</EmptyHeader>
								</Empty>
							</div>
						) : (
							<div className="flex flex-col">
								<Button
									variant="ghost"
									size="sm"
									className="justify-start font-normal"
									disabled={atRoot}
									onClick={() => {
										setSelectedFile(null);
										void load(parentPath(path, rootPath ?? path));
									}}
								>
									<CornerLeftUp />
									{t('workspaceFiles.parent')}
								</Button>
								{sortedEntries.map((entry) => {
									const entryPath = childPath(path, entry.name);
									const mediaType = mime.getType(entry.name) || '';
									const isOffice =
										!entry.is_dir && isOfficeDocument(entry.name, mediaType);
									const isSelected = selectedFile?.path === entryPath;
									return (
										<div
											key={entry.name}
											className={cn(
												'flex min-w-0 items-center gap-2 rounded-sm px-2 py-1.5',
												isSelected && 'bg-muted',
											)}
										>
											{entry.is_dir ? (
												<Button
													variant="ghost"
													size="sm"
													className="min-w-0 flex-1 justify-start gap-2 px-0 font-normal"
													onClick={() => {
														setSelectedFile(null);
														void load(entryPath);
													}}
												>
													<Folder className="size-4 shrink-0" />
													<span className="truncate">{entry.name}</span>
												</Button>
											) : (
												<>
													<File className="size-4 shrink-0 text-muted-foreground" />
													<span
														className="min-w-0 flex-1 truncate text-sm"
														title={entry.name}
													>
														{entry.name}
													</span>
													<span className="shrink-0 text-xs text-muted-foreground">
														{fileSize(entry.size_bytes)}
													</span>
													{isOffice && (
														<Button
															size="sm"
															variant={
																isSelected ? 'secondary' : 'ghost'
															}
															onClick={() =>
																setSelectedFile({
																	name: entry.name,
																	path: entryPath,
																})
															}
														>
															{t('workspaceFiles.preview')}
														</Button>
													)}
													<Button
														variant="ghost"
														size="icon-sm"
														title={t('workspaceFiles.download')}
														aria-label={t(
															'workspaceFiles.downloadFile',
															{
																name: entry.name,
															},
														)}
														disabled={downloadPath === entryPath}
														onClick={() =>
															void download(entryPath, entry.name)
														}
													>
														{downloadPath === entryPath ? (
															<Loader2 className="animate-spin" />
														) : (
															<Download />
														)}
													</Button>
												</>
											)}
										</div>
									);
								})}
							</div>
						)}
					</div>
					{selectedFile && selectedEntry && (
						<OfficeFilePreview
							key={selectedFile.path}
							agentId={agentId!}
							sessionId={sessionId!}
							name={selectedFile.name}
							mediaType={
								mime.getType(selectedFile.name) || 'application/octet-stream'
							}
							loadOriginal={() => openFile(selectedFile)}
							getDownloadUrl={async () => {
								const { token } = await workspaceApi.downloadToken(
									agentId!,
									sessionId!,
									selectedFile.path,
								);
								return workspaceApi.downloadUrl(
									agentId!,
									sessionId!,
									selectedFile.path,
									token,
								);
							}}
						/>
					)}
				</div>
			</DrawerContent>
		</Drawer>
	);
}
