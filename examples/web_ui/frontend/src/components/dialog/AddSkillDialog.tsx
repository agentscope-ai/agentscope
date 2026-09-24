import { CircleAlert, FolderUp, PlusCircle } from 'lucide-react';
import { useRef, useState } from 'react';
import type { ReactNode } from 'react';

import type { SkillView } from '@/api';
import type { UploadOptions } from '@/api/workspace';
import { Alert, AlertDescription } from '@/components/ui/alert.tsx';
import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar.tsx';
import { Button } from '@/components/ui/button.tsx';
import { Checkbox } from '@/components/ui/checkbox.tsx';
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
	DialogTrigger,
} from '@/components/ui/dialog.tsx';
import {
	Empty,
	EmptyDescription,
	EmptyHeader,
	EmptyMedia,
	EmptyTitle,
} from '@/components/ui/empty.tsx';
import {
	Item,
	ItemContent,
	ItemDescription,
	ItemGroup,
	ItemMedia,
	ItemTitle,
} from '@/components/ui/item.tsx';
import { Progress } from '@/components/ui/progress.tsx';
import { Spinner } from '@/components/ui/spinner.tsx';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs.tsx';
import { useSkills } from '@/hooks/useSkills.ts';
import { useTranslation } from '@/i18n/useI18n';

interface Props {
	children: ReactNode;
	/** Names already in this workspace, which cannot be picked again. */
	present: Set<string>;
	onUpload: (files: File[], options?: UploadOptions) => Promise<void>;
	onAddFromLibrary: (skillIds: string[]) => Promise<void>;
}

/**
 * Adds skills to a workspace, either from the ones already installed
 * or by picking a folder off disk.
 *
 * Mirrors the MCP dialog: two peer tabs, a fixed-height body, and a
 * footer outside the tabs so switching moves nothing but the content.
 */
export function AddSkillDialog({ children, present, onUpload, onAddFromLibrary }: Props) {
	const { t } = useTranslation();
	const [open, setOpen] = useState(false);
	const [tab, setTab] = useState('installed');
	const [picked, setPicked] = useState<Set<string>>(new Set());
	const [files, setFiles] = useState<File[]>([]);
	const [progress, setProgress] = useState<number | null>(null);
	const [busy, setBusy] = useState(false);
	const [error, setError] = useState<string | null>(null);
	const input = useRef<HTMLInputElement>(null);
	const { skills, loading } = useSkills();

	const selectable = skills.filter((skill) => !present.has(skill.name));

	const toggle = (skill: SkillView) => {
		setPicked((prev) => {
			const next = new Set(prev);
			if (next.has(skill.id)) next.delete(skill.id);
			else next.add(skill.id);
			return next;
		});
	};

	const close = () => {
		setOpen(false);
		setPicked(new Set());
		setFiles([]);
		setProgress(null);
		setError(null);
	};

	const handleAddPicked = async () => {
		setBusy(true);
		setError(null);
		try {
			await onAddFromLibrary([...picked]);
			close();
		} catch (e) {
			setError((e as Error).message);
		} finally {
			setBusy(false);
		}
	};

	const handleUpload = async () => {
		setBusy(true);
		setError(null);
		setProgress(0);
		try {
			await onUpload(files, {
				onProgress: ({ loaded, total }) =>
					setProgress(total ? Math.round((loaded / total) * 100) : 0),
			});
			close();
		} catch (e) {
			setError((e as Error).message);
			setProgress(null);
		} finally {
			setBusy(false);
		}
	};

	// The picked folder's own name, which is what the skill installs as.
	const root = files[0]?.webkitRelativePath.split('/')[0] ?? '';

	return (
		<Dialog open={open} onOpenChange={(next) => (next ? setOpen(true) : close())}>
			<DialogTrigger asChild>{children}</DialogTrigger>
			<DialogContent className="sm:max-w-2xl">
				<DialogHeader>
					<DialogTitle>{t('panel.skill.add')}</DialogTitle>
					<DialogDescription>{t('panel.skill.addDescription')}</DialogDescription>
				</DialogHeader>

				<Tabs value={tab} onValueChange={setTab}>
					<TabsList className="w-full">
						<TabsTrigger value="installed" className="flex-1">
							{t('panel.skill.fromInstalled')}
						</TabsTrigger>
						<TabsTrigger value="upload" className="flex-1">
							{t('panel.skill.fromFolder')}
						</TabsTrigger>
					</TabsList>

					{/* Fixed height on the shared container rather than per
					    tab, so the dialog does not resize as you switch. */}
					<div className="mt-4 h-80 overflow-y-auto scroll-fade">
						<TabsContent value="installed">
							{loading ? (
								<div className="flex justify-center py-10">
									<Spinner />
								</div>
							) : skills.length === 0 ? (
								<Empty className="border-none py-10">
									<EmptyHeader>
										<EmptyMedia variant="icon">
											<PlusCircle />
										</EmptyMedia>
										<EmptyTitle>
											{t('panel.skill.installedEmptyTitle')}
										</EmptyTitle>
										<EmptyDescription>
											{t('panel.skill.installedEmptyDescription')}
										</EmptyDescription>
									</EmptyHeader>
								</Empty>
							) : (
								<ItemGroup className="gap-1">
									{skills.map((skill) => {
										const already = present.has(skill.name);
										return (
											<Item
												key={skill.id}
												data-disabled={already || undefined}
											>
												<Checkbox
													checked={already || picked.has(skill.id)}
													disabled={already}
													onCheckedChange={() => toggle(skill)}
												/>
												<ItemMedia>
													<Avatar className="rounded-md">
														<AvatarImage
															src={skill.icon_url ?? undefined}
															alt={skill.display_name || skill.name}
															loading="lazy"
														/>
														<AvatarFallback className="rounded-md">
															{(skill.display_name || skill.name)
																.slice(0, 1)
																.toUpperCase()}
														</AvatarFallback>
													</Avatar>
												</ItemMedia>
												<ItemContent>
													<ItemTitle>
														<span className="font-medium">
															{skill.display_name || skill.name}
														</span>
														{skill.author && (
															<span className="text-xs text-muted-foreground">
																@{skill.author}
															</span>
														)}
													</ItemTitle>
													<ItemDescription className="line-clamp-1">
														{skill.description}
													</ItemDescription>
												</ItemContent>
											</Item>
										);
									})}
								</ItemGroup>
							)}
						</TabsContent>

						<TabsContent value="upload">
							<input
								ref={input}
								type="file"
								className="hidden"
								// Non-standard but supported everywhere; React
								// has no typing for it, hence the cast.
								{...({
									webkitdirectory: '',
									directory: '',
								} as Record<string, string>)}
								onChange={(e) => {
									setFiles(Array.from(e.target.files ?? []));
									setError(null);
								}}
							/>
							{files.length === 0 ? (
								<Empty className="border-none py-10">
									<EmptyHeader>
										<EmptyMedia variant="icon">
											<FolderUp />
										</EmptyMedia>
										<EmptyTitle>{t('panel.skill.pickFolderTitle')}</EmptyTitle>
										<EmptyDescription>
											{t('panel.skill.pickFolderDescription')}
										</EmptyDescription>
									</EmptyHeader>
									<Button onClick={() => input.current?.click()}>
										<FolderUp />
										{t('panel.skill.pickFolder')}
									</Button>
								</Empty>
							) : (
								<ItemGroup className="gap-1">
									<Item variant="muted">
										<ItemMedia>
											<FolderUp />
										</ItemMedia>
										<ItemContent>
											<ItemTitle>
												<span className="font-medium">{root}</span>
											</ItemTitle>
											<ItemDescription>
												{t('panel.skill.fileCount', {
													count: files.length,
												})}
											</ItemDescription>
										</ItemContent>
										<Button
											variant="ghost"
											size="sm"
											disabled={busy}
											onClick={() => input.current?.click()}
										>
											{t('panel.skill.pickAnother')}
										</Button>
									</Item>
									{files.map((file) => (
										<Item key={file.webkitRelativePath} size="sm">
											<ItemContent>
												<ItemTitle>
													<span className="truncate font-mono text-xs">
														{file.webkitRelativePath}
													</span>
												</ItemTitle>
											</ItemContent>
											<span className="shrink-0 text-xs text-muted-foreground">
												{formatBytes(file.size)}
											</span>
										</Item>
									))}
								</ItemGroup>
							)}
						</TabsContent>
					</div>
				</Tabs>

				{progress !== null && <Progress value={progress} />}
				{error && (
					<Alert variant="destructive">
						<CircleAlert />
						{/* One line per skill that did not land, so the
						    joined message has to keep its newlines. */}
						<AlertDescription className="whitespace-pre-wrap">{error}</AlertDescription>
					</Alert>
				)}

				{/* Outside the tabs on purpose: the footer is the dialog's,
				    not either tab's, so its layout never shifts. */}
				<DialogFooter className="sm:justify-between">
					<span className="self-center text-xs text-muted-foreground">
						{tab === 'installed' && selectable.length > 0
							? t('common.selectedCount', {
									selected: picked.size,
									total: selectable.length,
								})
							: null}
					</span>
					<div className="flex items-center gap-x-2">
						<Button variant="ghost" onClick={close} disabled={busy}>
							{t('common.cancel')}
						</Button>
						{tab === 'installed' ? (
							<Button onClick={handleAddPicked} disabled={picked.size === 0 || busy}>
								{busy ? <Spinner /> : <PlusCircle />}
								{t('common.add')}
							</Button>
						) : (
							<Button onClick={handleUpload} disabled={files.length === 0 || busy}>
								{busy ? <Spinner /> : <PlusCircle />}
								{t('common.add')}
							</Button>
						)}
					</div>
				</DialogFooter>
			</DialogContent>
		</Dialog>
	);
}

/** Renders a byte count at the largest unit that keeps it readable. */
function formatBytes(bytes: number): string {
	if (bytes < 1024) return `${bytes} B`;
	if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
	return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}
