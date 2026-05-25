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

interface AddSkillDialogProps {
	children: ReactNode;
	onAdd: (skillPath: string) => Promise<void>;
}

export function AddSkillDialog({ children, onAdd }: AddSkillDialogProps) {
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
			<DialogContent>
				<DialogHeader>
					<DialogTitle>Add Skill</DialogTitle>
					<DialogDescription>
						Enter the path to a skill directory to add it to the workspace.
					</DialogDescription>
				</DialogHeader>
				<div className="flex flex-col gap-y-2">
					<Label htmlFor="skill-path">Skill Path</Label>
					<Input
						id="skill-path"
						placeholder="/path/to/skill"
						value={skillPath}
						onChange={(e) => setSkillPath(e.target.value)}
						onKeyDown={(e) => {
							if (e.key === 'Enter') handleSubmit();
						}}
					/>
					{error && <p className="text-destructive text-sm">{error}</p>}
				</div>
				<DialogFooter>
					<Button variant="outline" onClick={() => setOpen(false)} disabled={loading}>
						Cancel
					</Button>
					<Button onClick={handleSubmit} disabled={loading || !skillPath.trim()}>
						{loading ? 'Adding…' : 'Add'}
					</Button>
				</DialogFooter>
			</DialogContent>
		</Dialog>
	);
}
