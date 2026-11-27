import {
	ArrowRight,
	ArrowUp,
	ChevronRight,
	FileText,
	Folder,
	FolderOpen,
	Home,
	Loader2,
	RotateCw,
	X,
} from 'lucide-react';
import { useEffect, useEffectEvent, useLayoutEffect, useMemo, useRef, useState } from 'react';

import { ArtifactPreviewContent } from './ArtifactDrawer';
import {
	type FileChanges,
	fileChangeVersion,
	type FileTarget,
	workspaceChildPath,
} from './artifacts';
import { useFileLeaveGuard } from './useFileLeaveGuard';
import { ApiError } from '@/api/client';
import type { DirectoryListing } from '@/api/types';
import { workspaceApi } from '@/api/workspace';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
	Sheet,
	SheetClose,
	SheetContent,
	SheetDescription,
	SheetHeader,
	SheetTitle,
} from '@/components/ui/sheet';
import { useTranslation } from '@/i18n/useI18n';

interface Props {
	agentId: string;
	sessionId: string;
	initialPath: string;
	onClose: () => void;
	onReference: (path: string) => void;
	referenceDisabled?: boolean;
	fileChanges: FileChanges;
}

/** One session-scoped drawer; returning from preview keeps the directory state. */
export function WorkspaceFilesDrawer({
	agentId,
	sessionId,
	initialPath,
	onClose,
	onReference,
	referenceDisabled,
	fileChanges,
}: Props) {
	const { t, i18n } = useTranslation();
	const [request, setRequest] = useState({ path: initialPath, revision: 0, background: false });
	const [pathInput, setPathInput] = useState(initialPath);
	const [listing, setListing] = useState<DirectoryListing | null>(null);
	const [loading, setLoading] = useState(true);
	const [error, setError] = useState<'directoryMissing' | 'directoryError' | null>(null);
	const [selected, setSelected] = useState<FileTarget | null>(null);
	const listRef = useRef<HTMLDivElement>(null);
	const contentRef = useRef<HTMLDivElement>(null);
	const titleRef = useRef<HTMLHeadingElement>(null);
	const scrollPosition = useRef(0);
	const lastFile = useRef<string | null>(null);
	const referencing = useRef(false);
	const guard = useFileLeaveGuard();
	const previousChanges = useRef(fileChanges);

	const refreshDirectory = useEffectEvent((previous: FileChanges) => {
		if (
			listing &&
			fileChangeVersion(previous, listing.path, true) ===
				fileChangeVersion(fileChanges, listing.path, true)
		)
			return;
		setRequest((current) => ({
			path: listing?.path ?? current.path,
			revision: current.revision + 1,
			background: true,
		}));
	});
	useEffect(() => {
		if (previousChanges.current === fileChanges) return;
		refreshDirectory(previousChanges.current);
		previousChanges.current = fileChanges;
	}, [fileChanges]);

	useEffect(() => {
		const controller = new AbortController();
		setLoading(true);
		setError(null);
		void workspaceApi
			.directories(agentId, sessionId, request.path, {
				signal: controller.signal,
				silent: true,
			})
			.then((next) => {
				if (controller.signal.aborted) return;
				setListing(next);
				if (!request.background) setPathInput(next.path);
			})
			.catch((cause: unknown) => {
				if (controller.signal.aborted) return;
				setError(
					cause instanceof ApiError && cause.status === 404
						? 'directoryMissing'
						: 'directoryError',
				);
			})
			.finally(() => {
				if (!controller.signal.aborted) setLoading(false);
			});
		return () => controller.abort();
	}, [agentId, sessionId, request]);

	useLayoutEffect(() => {
		if (selected) {
			contentRef.current?.querySelector('button')?.focus({ preventScroll: true });
		} else if (listRef.current) {
			listRef.current.scrollTop = scrollPosition.current;
			const previous = Array.from(
				listRef.current.querySelectorAll<HTMLButtonElement>('button[data-path]'),
			).find((button) => button.dataset.path === lastFile.current);
			previous?.focus({ preventScroll: true });
		}
	}, [selected]);

	const entries = useMemo(() => {
		const collator = new Intl.Collator(i18n.language, { numeric: true });
		return [...(listing?.entries ?? [])].sort(
			(a, b) => Number(b.is_dir) - Number(a.is_dir) || collator.compare(a.name, b.name),
		);
	}, [listing, i18n.language]);
	const sizeFormat = useMemo(
		() =>
			new Intl.NumberFormat(i18n.language, {
				style: 'unit',
				unit: 'byte',
				notation: 'compact',
				maximumFractionDigits: 1,
			}),
		[i18n.language],
	);

	const navigate = (path: string) => {
		scrollPosition.current = 0;
		lastFile.current = null;
		setPathInput(path);
		setListing(null);
		setLoading(true);
		setRequest((current) => ({ path, revision: current.revision + 1, background: false }));
	};
	const refresh = () => {
		setRequest((current) => ({
			path: listing?.path ?? current.path,
			revision: current.revision + 1,
			background: true,
		}));
	};

	return (
		<Sheet
			open
			onOpenChange={(open) => {
				if (!open && guard.confirmLeave()) onClose();
			}}
		>
			<SheetContent
				ref={contentRef}
				className="gap-0 data-[side=right]:w-full data-[side=right]:sm:max-w-2xl"
				showCloseButton={false}
				onOpenAutoFocus={(event) => {
					event.preventDefault();
					titleRef.current?.focus();
				}}
				onCloseAutoFocus={(event) => {
					if (referencing.current) event.preventDefault();
				}}
			>
				{guard.navigationGuard}
				{selected ? (
					<ArtifactPreviewContent
						key={selected.path}
						artifact={selected}
						changeVersion={fileChangeVersion(fileChanges, selected.path)}
						agentId={agentId}
						sessionId={sessionId}
						onEditStateChange={guard.setEditState}
						onBack={() => {
							if (guard.confirmLeave()) setSelected(null);
						}}
						onReference={(path) => {
							if (!guard.confirmLeave()) return;
							referencing.current = true;
							onReference(path);
						}}
						referenceDisabled={referenceDisabled}
					/>
				) : (
					<>
						<SheetHeader className="gap-2 border-b p-5">
							<div className="flex min-w-0 items-center gap-3">
								<FolderOpen className="size-5 shrink-0 text-muted-foreground" />
								<SheetTitle
									ref={titleRef}
									tabIndex={-1}
									className="flex-1 outline-none"
								>
									{t('artifacts.browseFiles')}
								</SheetTitle>
								<SheetClose asChild>
									<Button
										variant="ghost"
										size="icon-sm"
										aria-label={t('common.close')}
									>
										<X />
									</Button>
								</SheetClose>
							</div>
							<SheetDescription>{t('artifacts.browseDescription')}</SheetDescription>
							<form
								className="mt-2 flex items-center gap-2"
								onSubmit={(event) => {
									event.preventDefault();
									navigate(pathInput);
								}}
							>
								<Input
									aria-label={t('artifacts.directoryPath')}
									value={pathInput}
									onChange={(event) => setPathInput(event.target.value)}
									placeholder={t('artifacts.directoryPath')}
									className="font-mono"
									autoComplete="off"
									spellCheck={false}
								/>
								<Button
									type="submit"
									variant="outline"
									size="icon"
									aria-label={t('artifacts.goToPath')}
								>
									<ArrowRight />
								</Button>
							</form>
							<div className="flex flex-wrap items-center gap-1">
								<Button
									variant="ghost"
									size="sm"
									disabled={!listing || loading}
									onClick={() => {
										if (listing)
											navigate(workspaceChildPath(listing.path, '..'));
									}}
								>
									<ArrowUp />
									{t('artifacts.parentDirectory')}
								</Button>
								<Button
									variant="ghost"
									size="sm"
									onClick={() => navigate(initialPath)}
								>
									<Home />
									{t('artifacts.workingDirectory')}
								</Button>
								<Button
									variant="ghost"
									size="sm"
									disabled={loading}
									onClick={refresh}
								>
									<RotateCw />
									{t('artifacts.refresh')}
								</Button>
							</div>
						</SheetHeader>
						<div
							ref={listRef}
							role="region"
							aria-label={t('artifacts.directoryContents')}
							aria-busy={loading}
							onScroll={(event) => {
								scrollPosition.current = event.currentTarget.scrollTop;
							}}
							className="min-h-0 flex-1 overflow-auto p-3"
						>
							{error && (
								<div
									role="alert"
									className="space-y-2 p-3 text-sm text-muted-foreground"
								>
									<p>{t(`artifacts.${error}`)}</p>
									<Button
										variant="outline"
										size="sm"
										disabled={loading}
										onClick={refresh}
									>
										<RotateCw />
										{t('artifacts.retry')}
									</Button>
								</div>
							)}
							{loading && !listing ? (
								<div
									role="status"
									className="flex h-full items-center justify-center gap-2 p-6 text-sm text-muted-foreground"
								>
									<Loader2 className="size-4 animate-spin" />
									{t('common.loading')}
								</div>
							) : !listing ? null : entries.length === 0 ? (
								<p
									role="status"
									className="p-6 text-center text-sm text-muted-foreground"
								>
									{t('artifacts.directoryEmpty')}
								</p>
							) : (
								<ul className="space-y-1">
									{entries.map((entry) => {
										const path = workspaceChildPath(listing!.path, entry.name);
										return (
											<li key={entry.name}>
												<button
													type="button"
													data-path={path}
													title={entry.name}
													className="flex w-full min-w-0 items-center gap-3 rounded-lg px-3 py-3 text-left hover:bg-muted/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
													onClick={() => {
														if (entry.is_dir) navigate(path);
														else {
															scrollPosition.current =
																listRef.current?.scrollTop ?? 0;
															lastFile.current = path;
															setSelected({ path, name: entry.name });
														}
													}}
												>
													{entry.is_dir ? (
														<Folder className="size-5 shrink-0 text-muted-foreground" />
													) : (
														<FileText className="size-5 shrink-0 text-muted-foreground" />
													)}
													<span className="min-w-0 flex-1 truncate text-sm">
														{entry.name}
													</span>
													{entry.is_dir ? (
														<>
															<span className="sr-only">
																{t('artifacts.folder')}
															</span>
															<ChevronRight className="size-4 shrink-0 text-muted-foreground" />
														</>
													) : (
														entry.size_bytes !== null && (
															<span className="shrink-0 text-xs tabular-nums text-muted-foreground">
																{sizeFormat.format(
																	entry.size_bytes,
																)}
															</span>
														)
													)}
												</button>
											</li>
										);
									})}
								</ul>
							)}
						</div>
					</>
				)}
			</SheetContent>
		</Sheet>
	);
}
