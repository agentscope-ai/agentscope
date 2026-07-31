import { FolderOpen, Loader2, PlusCircle, X } from 'lucide-react';
import { useRef, useState, type ChangeEvent, type ReactNode } from 'react';
import { parse } from 'yaml';

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
import { Label } from '@/components/ui/label';
import { useTranslation } from '@/i18n/useI18n';

interface AddSkillDialogProps {
	children: ReactNode;
	onAdd: (files: File[]) => Promise<void>;
}

export function AddSkillDialog({ children, onAdd }: AddSkillDialogProps) {
	const { t } = useTranslation();
	const inputRef = useRef<HTMLInputElement>(null);
	const [open, setOpen] = useState(false);
	const [files, setFiles] = useState<File[]>([]);
	const [folderName, setFolderName] = useState<string | null>(null);
	const [loading, setLoading] = useState(false);
	const [error, setError] = useState<string | null>(null);

	const resetSelection = () => {
		setFiles([]);
		setFolderName(null);
		setError(null);
	};

	const handleOpenChange = (nextOpen: boolean) => {
		if (!nextOpen && loading) return;
		setOpen(nextOpen);
		if (!nextOpen) resetSelection();
	};

	const handleSelection = async (event: ChangeEvent<HTMLInputElement>) => {
		const selectedFiles = Array.from(event.currentTarget.files ?? []);
		event.currentTarget.value = '';
		resetSelection();
		if (!selectedFiles.length) return;

		const paths = selectedFiles.map((file) => file.webkitRelativePath || file.name);
		const parts = paths.map((path) => path.split('/'));
		const validPaths = parts.every(
			(pathParts) =>
				pathParts.length >= 2 &&
				pathParts.every((part) => part && part !== '.' && part !== '..'),
		);
		const roots = new Set(parts.map((pathParts) => pathParts[0]));
		if (!validPaths || roots.size !== 1) {
			setError(t('dialog-skill-add.invalidSelection'));
			return;
		}

		const root = parts[0][0];
		const skillFile = selectedFiles.find((_, index) => {
			const pathParts = parts[index];
			return pathParts.length === 2 && pathParts[1] === 'SKILL.md';
		});
		if (!skillFile) {
			setError(t('dialog-skill-add.missingSkillMd'));
			return;
		}

		try {
			const content = (await skillFile.text()).replace(/^\uFEFF/, '');
			const frontmatter = content.match(/^---[ \t]*\r?\n([\s\S]*?)\r?\n---[ \t]*(?:\r?\n|$)/);
			if (!frontmatter) throw new Error('missing frontmatter');
			const metadata = parse(frontmatter[1]) as Record<string, unknown> | null;
			if (
				!metadata ||
				typeof metadata.name !== 'string' ||
				!metadata.name.trim() ||
				typeof metadata.description !== 'string' ||
				!metadata.description.trim()
			) {
				throw new Error('invalid frontmatter');
			}
		} catch {
			setError(t('dialog-skill-add.invalidSkillMd'));
			return;
		}

		setFiles(selectedFiles);
		setFolderName(root);
	};

	const handleSubmit = async () => {
		if (!files.length) return;
		setLoading(true);
		setError(null);
		try {
			await onAdd(files);
			resetSelection();
			setOpen(false);
		} catch (e) {
			setError((e as Error).message);
		} finally {
			setLoading(false);
		}
	};

	return (
		<Dialog open={open} onOpenChange={handleOpenChange}>
			<DialogTrigger asChild>{children}</DialogTrigger>
			<DialogContent className="!w-[500px] !max-w-[500px]">
				<DialogHeader>
					<DialogTitle>{t('dialog-skill-add.title')}</DialogTitle>
					<DialogDescription>{t('dialog-skill-add.description')}</DialogDescription>
				</DialogHeader>
				<div className="flex flex-col gap-y-2">
					<Label htmlFor="skill-folder">{t('dialog-skill-add.folderLabel')}</Label>
					<input
						ref={inputRef}
						id="skill-folder"
						type="file"
						className="hidden"
						multiple
						{...{ webkitdirectory: '' }}
						onChange={(event) => void handleSelection(event)}
					/>
					<div className="flex min-w-0 items-center gap-3">
						<Button
							type="button"
							variant="outline"
							onClick={() => inputRef.current?.click()}
						>
							<FolderOpen className="size-3.5" />
							{t('dialog-skill-add.selectFolder')}
						</Button>
						<span className="min-w-0 truncate text-sm text-muted-foreground">
							{folderName ?? t('dialog-skill-add.noFolderSelected')}
						</span>
					</div>
					{error && <p className="text-destructive text-sm">{error}</p>}
				</div>
				<DialogFooter>
					<Button
						variant="ghost"
						onClick={() => handleOpenChange(false)}
						disabled={loading}
					>
						<X className="size-3.5" />
						{t('common.cancel')}
					</Button>
					<Button onClick={handleSubmit} disabled={loading || !files.length}>
						{loading ? (
							<Loader2 className="size-3.5 animate-spin" />
						) : (
							<PlusCircle className="size-3.5" />
						)}
						{loading ? t('dialog-mcp-create.adding') : t('common.add')}
					</Button>
				</DialogFooter>
			</DialogContent>
		</Dialog>
	);
}
