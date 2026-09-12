import {
	ChevronDown,
	CircleAlert,
	CheckCircle,
	CornerLeftUp,
	File,
	Folder,
	FolderOpen,
	Home,
	Loader2,
	CornerDownLeft,
} from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';

import { InputGroup, InputGroupAddon, InputGroupButton, InputGroupInput } from '../ui/input-group';
import type { DirectoryEntry } from '@/api';
import { workspaceApi } from '@/api';
import { Button } from '@/components/ui/button';
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
	DialogTrigger,
} from '@/components/ui/dialog';
import {
	Empty,
	EmptyDescription,
	EmptyHeader,
	EmptyMedia,
	EmptyTitle,
} from '@/components/ui/empty';
import { Spinner } from '@/components/ui/spinner';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { useTranslation } from '@/i18n/useI18n';
import { formatApiErrorForAlert } from '@/lib/api-error';

interface WorkingDirectoryDialogProps {
	agentId: string | null;
	sessionId: string | null;
	/**
	 * The session's current working directory. Absolute, or relative to
	 * the workspace root; `null` is the root itself.
	 */
	value: string | null;
	/**
	 * Called with the newly picked directory. Rejections are the caller's
	 * to surface; the dialog only closes once the promise settles.
	 */
	onChange: (cwd: string | null) => void | Promise<void>;
	disabled?: boolean;
	className?: string;
}

/** The last segment of a path, which is what a folder is called. */
function basename(path: string): string {
	const trimmed = path.replace(/\/+$/, '');
	const cut = trimmed.lastIndexOf('/');
	return cut === -1 ? trimmed : trimmed.slice(cut + 1);
}

/**
 * A folder picker for the session's working directory.
 *
 * Hands back a **path**, never a file — nothing is uploaded and nothing
 * is read. Only directories are listed, since a file is never a valid
 * answer.
 *
 * Browsing is not confined to the workspace root: the picker walks
 * wherever `GET /workspace/directories` can reach, which is the sandbox
 * on a container backend and the host on a local one. Because the root
 * is backend-dependent, the browser never knows where it is until the
 * server says so — hence every listing is addressed by the absolute path
 * the previous response echoed back, and only the initial "open at the
 * session's directory" request uses the stored (possibly relative) value.
 *
 * A dialog rather than a popover: finding a folder anywhere on the
 * filesystem is a task, not a quick toggle, so it earns the room and the
 * deliberate dismissal.
 *
 * @param agentId - Agent owning the session. `null` disables the trigger.
 * @param sessionId - Session whose workspace is browsed.
 * @param value - Current working directory, or `null` for the root.
 * @param onChange - Receives the picked directory.
 * @param disabled - Locks the trigger.
 * @param className - Extra classes for the trigger button.
 * @returns The trigger button plus its dialog.
 */
export function WorkingDirectoryDialog({
	agentId,
	sessionId,
	value,
	onChange,
	disabled = false,
	className,
}: WorkingDirectoryDialogProps) {
	const { t } = useTranslation();
	const [open, setOpen] = useState(false);
	// The answer, and the single source of truth: whatever the input
	// shows is what Confirm submits. Browsing is just another way to fill
	// it in. Splitting this into "the browsed path" and "the typed path"
	// is what let an edited-but-unsubmitted input silently save the old
	// value instead.
	const [path, setPath] = useState('');
	// The listing currently on screen, and the path it came from — which
	// trails `path` while the user is mid-edit.
	const [listedPath, setListedPath] = useState<string | null>(null);
	const [entries, setEntries] = useState<DirectoryEntry[]>([]);
	// Starts true because the effect below runs after the first paint —
	// leaving it false would flash an empty listing over a directory that
	// is about to arrive.
	const [loading, setLoading] = useState(true);
	// Whatever went wrong last, listing or saving. Shown in place of the
	// list — one home for both, so nothing is reported twice.
	const [error, setError] = useState<string | null>(null);
	const [saving, setSaving] = useState(false);
	// Bumped by every browse, so an overtaken listing cannot land.
	const reqId = useRef(0);

	const load = useCallback(
		async (target: string) => {
			// Only the newest browse may write: a slow parent directory
			// answering after a fast child would silently bounce the user
			// back up a level.
			const id = ++reqId.current;
			if (!agentId || !sessionId) {
				setLoading(false);
				return;
			}
			setLoading(true);
			setError(null);
			try {
				const listing = await workspaceApi.directories(agentId, sessionId, target);
				if (id !== reqId.current) return;
				// Files are listed but not selectable — a directory with its
				// files hidden is hard to recognise, and "empty" would be a
				// lie for a folder that is merely all files. Order is the
				// backend's; the icon already says which is which.
				setEntries(listing.entries);
				// Adopt the resolved path: the server normalises `..` and
				// turns a relative request absolute, so this is also how the
				// input learns where the workspace root actually is.
				setPath(listing.path);
				setListedPath(listing.path);
			} catch (e) {
				if (id !== reqId.current) return;
				// `path` is left alone — it is what the user asked for, and
				// blanking it would erase what they typed. `listedPath`
				// staying null is what disables Confirm.
				setListedPath(null);
				setEntries([]);
				// Inline, not a toast: the dialog is the only thing the user
				// is looking at, and a directory that has moved is a normal
				// outcome of browsing a workspace an agent is changing under
				// you.
				setError(formatApiErrorForAlert(e));
			} finally {
				if (id === reqId.current) setLoading(false);
			}
		},
		[agentId, sessionId],
	);

	// Re-open always restarts from the session's committed directory, so
	// a cancelled browse does not leak into the next one.
	useEffect(() => {
		if (open) void load(value ?? '');
	}, [open, value, load]);

	const handleConfirm = async () => {
		setSaving(true);
		setError(null);
		try {
			await onChange(path);
			setOpen(false);
		} catch (e) {
			// Most likely a 409 — the session started running while the
			// dialog was open. Staying open with the reason on screen beats
			// closing as if it worked. The list is fine, so this is the one
			// failure the Alert still has to carry.
			setError(formatApiErrorForAlert(e));
		} finally {
			setSaving(false);
		}
	};

	const label = value ? basename(value) : t('workdir.root');

	return (
		<Dialog open={open} onOpenChange={setOpen}>
			<DialogTrigger asChild>
				<Button
					variant="secondary"
					size="sm"
					disabled={disabled || !agentId || !sessionId}
					className={className}
					title={value ?? t('workdir.root')}
				>
					<Folder />
					<span className="truncate max-w-40">{label}</span>
					<ChevronDown className="text-muted-foreground" />
				</Button>
			</DialogTrigger>
			<DialogContent className="sm:max-w-lg">
				<DialogHeader>
					<DialogTitle>{t('workdir.title')}</DialogTitle>
					<DialogDescription>{t('workdir.description')}</DialogDescription>
				</DialogHeader>

				{/* `min-w-0` all the way down: without it a long entry name
				    widens this column, and the dialog with it, instead of
				    being elided inside its own row. */}
				<div className="flex min-w-0 flex-col gap-y-2">
					<form
						onSubmit={(e) => {
							e.preventDefault();
							void load(path);
						}}
					>
						<InputGroup>
							<InputGroupInput
								value={path}
								spellCheck={false}
								className="font-mono text-[0.8rem]! tracking-tighter"
								placeholder={t('workdir.pathPlaceholder')}
								onChange={(e) => {
									setPath(e.target.value);
									// The failed save was for a different path;
									// the listing keeps its own error until the
									// next load replaces it.
									setError(null);
								}}
							/>
							<InputGroupAddon>
								<Tooltip>
									<TooltipTrigger asChild>
										<InputGroupButton
											type="button"
											onClick={() => void load('')}
										>
											<Home />
										</InputGroupButton>
									</TooltipTrigger>
									<TooltipContent>{t('workdir.root')}</TooltipContent>
								</Tooltip>
							</InputGroupAddon>
							<InputGroupAddon align="inline-end">
								{/* Submits the form, so clicking and pressing
							    Enter go through the same handler. */}
								<InputGroupButton type="submit">
									<CornerDownLeft />
								</InputGroupButton>
							</InputGroupAddon>
						</InputGroup>
					</form>
					<div className="h-[50vh] min-w-0 overflow-y-auto border rounded-[1rem] p-1">
						{loading ? (
							<div className="flex h-full items-center justify-center">
								<Spinner className="text-muted-foreground" />
							</div>
						) : error ? (
							<Empty className="h-full border-0 p-4">
								<EmptyHeader>
									<EmptyMedia variant="icon">
										<FolderOpen />
									</EmptyMedia>
									<EmptyTitle>{t('workdir.errorTitle')}</EmptyTitle>
									{/* The title says a listing failed; this says
									    why — missing, a file, unreadable. */}
									<EmptyDescription className="break-all">
										{error}
									</EmptyDescription>
								</EmptyHeader>
							</Empty>
						) : (
							<>
								{/* Always offered: the server resolves `..` against
							    wherever we are, so there is no floor to
							    special-case. */}
								<Button
									variant="ghost"
									size="sm"
									className="w-full justify-start gap-2 font-normal"
									onClick={() => void load(`${listedPath ?? ''}/..`)}
								>
									<CornerLeftUp className="size-3.5 shrink-0" />
									{t('workdir.parent')}
								</Button>
								{entries.length === 0 ? (
									<p className="px-2 py-3 text-center text-xs text-muted-foreground">
										{t('workdir.empty')}
									</p>
								) : (
									entries.map((entry) => (
										<Button
											key={entry.name}
											variant="ghost"
											size="sm"
											// A file is context, never an answer: it is
											// listed so the folder is recognisable, but
											// only a directory can be picked.
											disabled={!entry.is_dir}
											// `min-w-0` is what makes the child's
											// `truncate` work: a flex item defaults to
											// min-width:auto, so a long name widens the
											// row instead of eliding inside it.
											className="w-full min-w-0 justify-start gap-2 font-normal"
											onClick={() =>
												void load(`${listedPath ?? ''}/${entry.name}`)
											}
										>
											{entry.is_dir ? (
												<Folder className="size-3.5 shrink-0" />
											) : (
												<File className="size-3.5 shrink-0" />
											)}
											<span className="min-w-0 truncate">{entry.name}</span>
										</Button>
									))
								)}
							</>
						)}
					</div>
				</div>
				<DialogFooter>
					<Button variant="ghost" onClick={() => setOpen(false)} disabled={saving}>
						<CircleAlert className="size-3.5" />
						{t('common.cancel')}
					</Button>
					<Button
						onClick={() => void handleConfirm()}
						disabled={saving || listedPath !== path}
						title={listedPath === path ? undefined : t('workdir.pressEnter')}
					>
						{saving ? (
							<Loader2 className="size-3.5 animate-spin" />
						) : (
							<CheckCircle className="size-3.5" />
						)}
						{t('common.confirm')}
					</Button>
				</DialogFooter>
			</DialogContent>
		</Dialog>
	);
}
