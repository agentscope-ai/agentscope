import { ChevronDown } from 'lucide-react';

import type { ChunkerInfo } from '@/api';
import { Button } from '@/components/ui/button';
import {
	DropdownMenu,
	DropdownMenuContent,
	DropdownMenuItem,
	DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { useTranslation } from '@/i18n/useI18n.ts';

interface Props {
	value: string;
	chunkers: ChunkerInfo[];
	loading?: boolean;
	onChange?: (type: string) => void;
	disabled?: boolean;
	placeholder?: string;
}

/**
 * Chunker type picker. Mirrors the same visual style used by
 * :component:`EmbeddingSelect` so the two selectors look consistent
 * when placed next to each other in the knowledge-base creation dialog.
 */
export function ChunkerSelect({
	value,
	chunkers,
	loading,
	onChange,
	disabled,
	placeholder,
}: Props) {
	const { t } = useTranslation();

	const localizedLabel = (type: string) =>
		t(`chunker-types.${type}.label`, { defaultValue: type });

	const displayLabel = value
		? localizedLabel(value)
		: loading
			? t('chunker-select.loading')
			: (placeholder ?? t('chunker-select.placeholder'));

	return (
		<DropdownMenu>
			<DropdownMenuTrigger asChild>
				<Button
					variant="outline"
					size="sm"
					className="justify-between gap-1"
					disabled={disabled}
				>
					<span className="truncate">{displayLabel}</span>
					<ChevronDown className="size-3.5 opacity-50" />
				</Button>
			</DropdownMenuTrigger>
			<DropdownMenuContent align="start" className="min-w-48 max-h-72 overflow-y-auto">
				{!loading && chunkers.length === 0 ? (
					<div className="px-2 py-3 text-center text-sm text-muted-foreground">
						{t('chunker-select.empty')}
					</div>
				) : (
					chunkers.map((c) => (
						<DropdownMenuItem key={c.type} onSelect={() => onChange?.(c.type)}>
							{localizedLabel(c.type)}
						</DropdownMenuItem>
					))
				)}
			</DropdownMenuContent>
		</DropdownMenu>
	);
}
