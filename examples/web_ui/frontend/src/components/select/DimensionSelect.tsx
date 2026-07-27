import { ChevronDown } from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
	DropdownMenu,
	DropdownMenuContent,
	DropdownMenuItem,
	DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { useTranslation } from '@/i18n/useI18n.ts';

interface Props {
	/** Currently selected dimension. */
	value?: number | null;
	/** Allowed dimensions. ``null`` means no model picked yet. */
	options: number[] | null;
	onChange?: (dimension: number) => void;
	disabled?: boolean;
}

/**
 * Dimension picker for embedding models. Renders:
 *
 * - a placeholder when no model is selected;
 * - a read-only badge when only one dimension is allowed
 *   (fixed-dim model, or matryoshka narrowed by server policy);
 * - a dropdown when multiple dimensions are allowed (matryoshka
 *   under an ``ANY`` policy).
 */
export function DimensionSelect({ value, options, onChange, disabled }: Props) {
	const { t } = useTranslation();

	if (options === null || options.length === 0) {
		return (
			<span className="text-sm text-muted-foreground">
				{t('dimension-select.pickModelFirst')}
			</span>
		);
	}

	if (options.length === 1) {
		return (
			<Badge variant="secondary" className="font-mono">
				{options[0]}d
			</Badge>
		);
	}

	const display = value != null ? `${value}d` : t('dimension-select.placeholder');

	return (
		<DropdownMenu>
			<DropdownMenuTrigger asChild>
				<Button
					variant="outline"
					size="sm"
					className="justify-between gap-1"
					disabled={disabled}
				>
					<span className="truncate font-mono">{display}</span>
					<ChevronDown className="size-3.5 opacity-50" />
				</Button>
			</DropdownMenuTrigger>
			<DropdownMenuContent align="start" className="min-w-32">
				{options.map((dim) => (
					<DropdownMenuItem key={dim} onSelect={() => onChange?.(dim)}>
						<span className="font-mono">{dim}d</span>
					</DropdownMenuItem>
				))}
			</DropdownMenuContent>
		</DropdownMenu>
	);
}
