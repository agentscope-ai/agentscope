import { CircleAlert, Loader2, PlusCircle } from 'lucide-react';
import { useState, type ReactNode } from 'react';

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
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { useTranslation } from '@/i18n/useI18n';

interface AddSkillDialogProps {
	children: ReactNode;
	onAdd: (skillPath: string) => Promise<void>;
}

export function AddSkillDialog({ children, onAdd }: AddSkillDialogProps) {
	const { t } = useTranslation();
	const [open, setOpen] = useState(false);
	const [skillPath, setSkillPath] = useState('');
	const [loading, setLoading] = useState(false);
	const [error, setError] = useState<string | null>(null);

	const handleSubmit = async () => {
		if (!skillPath.trim()) return;
		setLoading(true);
		setError(null);
		try {
			await onAdd(skillPath.trim());
			setSkillPath('');
			setOpen(false);
		} catch (e) {
			setError((e as Error).message);
		} finally {
			setLoading(false);
		}
	};

	return (
		<Dialog open={open} onOpenChange={setOpen}>
			<DialogTrigger asChild>{children}</DialogTrigger>
			<DialogContent className="!w-[500px] !max-w-[500px]">
				<DialogHeader>
					<DialogTitle>{t('dialog-skill-add.title')}</DialogTitle>
					<DialogDescription>{t('dialog-skill-add.description')}</DialogDescription>
				</DialogHeader>
				<div className="flex flex-col gap-y-2">
					<Label htmlFor="skill-path">{t('dialog-skill-add.pathLabel')}</Label>
					<Input
						id="skill-path"
						placeholder={t('dialog-skill-add.pathPlaceholder')}
						value={skillPath}
						onChange={(e) => setSkillPath(e.target.value)}
						onKeyDown={(e) => {
							if (e.key === 'Enter') handleSubmit();
						}}
					/>
					{error && <p className="text-destructive text-sm">{error}</p>}
				</div>
				<DialogFooter>
					<Button variant="ghost" onClick={() => setOpen(false)} disabled={loading}>
						<CircleAlert className="size-3.5" />
						{t('common.cancel')}
					</Button>
					<Button onClick={handleSubmit} disabled={loading || !skillPath.trim()}>
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
