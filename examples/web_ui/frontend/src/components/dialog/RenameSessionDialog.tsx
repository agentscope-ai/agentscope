import { CheckCircle, CircleAlert, Loader2 } from 'lucide-react';
import { useState, useEffect } from 'react';

import { Button } from '@/components/ui/button';
import {
	Dialog,
	DialogContent,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from '@/components/ui/dialog';
import { Field, FieldGroup, FieldLabel } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { useTranslation } from '@/i18n/useI18n';

interface Props {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	currentName: string;
	onConfirm: (name: string) => Promise<void>;
}

export function RenameSessionDialog({ open, onOpenChange, currentName, onConfirm }: Props) {
	const { t } = useTranslation();
	const [name, setName] = useState(currentName);
	const [loading, setLoading] = useState(false);

	useEffect(() => {
		if (open) setName(currentName);
	}, [open, currentName]);

	const handleConfirm = async () => {
		if (!name.trim()) return;
		setLoading(true);
		try {
			await onConfirm(name.trim());
			onOpenChange(false);
		} finally {
			setLoading(false);
		}
	};

	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogContent>
				<DialogHeader>
					<DialogTitle>{t('dialog-session-rename.title')}</DialogTitle>
				</DialogHeader>
				<FieldGroup>
					<Field>
						<FieldLabel>{t('dialog-session-rename.label')}</FieldLabel>
						<Input
							value={name}
							onChange={(e) => setName(e.target.value)}
							placeholder={t('dialog-session-rename.placeholder')}
							onKeyDown={(e) => {
								if (e.key === 'Enter') handleConfirm();
							}}
							autoFocus
						/>
					</Field>
				</FieldGroup>
				<DialogFooter>
					<Button variant="ghost" onClick={() => onOpenChange(false)} disabled={loading}>
						<CircleAlert className="size-3.5" />
						{t('common.cancel')}
					</Button>
					<Button onClick={handleConfirm} disabled={loading || !name.trim()}>
						{loading ? (
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
