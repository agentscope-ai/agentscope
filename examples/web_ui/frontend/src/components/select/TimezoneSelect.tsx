import { ChevronDown, Globe, Search } from 'lucide-react';
import * as React from 'react';

import { Button } from '@/components/ui/button';
import {
	DropdownMenu,
	DropdownMenuContent,
	DropdownMenuItem,
	DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Input } from '@/components/ui/input';
import { cn } from '@/lib/utils.ts';

const LOCAL_TIMEZONE = Intl.DateTimeFormat().resolvedOptions().timeZone;

const ALL_TIMEZONES: string[] = Intl.supportedValuesOf('timeZone');

interface Props {
	className?: string;
	value?: string;
	onChange?: (value: string) => void;
	disabled?: boolean;
}

export function TimezoneSelect({ className, value, onChange, disabled }: Props) {
	const [search, setSearch] = React.useState('');
	const inputRef = React.useRef<HTMLInputElement>(null);

	const filtered = React.useMemo(() => {
		if (!search.trim()) return ALL_TIMEZONES;
		const q = search.toLowerCase();
		return ALL_TIMEZONES.filter((tz) => tz.toLowerCase().includes(q));
	}, [search]);

	const displayLabel = value || LOCAL_TIMEZONE;

	return (
		<DropdownMenu
			onOpenChange={(open) => {
				if (open) setSearch('');
			}}
		>
			<DropdownMenuTrigger asChild>
				<Button
					variant="outline"
					size="sm"
					className={cn('justify-between gap-1', className)}
					disabled={disabled}
				>
					<div className="flex flex-row items-center gap-x-2">
						<Globe className="size-3.5" />
						<span className="truncate text-xs">{displayLabel}</span>
					</div>
					<ChevronDown className="size-3.5 opacity-50" />
				</Button>
			</DropdownMenuTrigger>
			<DropdownMenuContent align="start" className="w-64">
				<div className="flex items-center gap-2 px-2 py-1.5 border-b">
					<Search className="size-3.5 opacity-50" />
					<Input
						ref={inputRef}
						value={search}
						onChange={(e) => setSearch(e.target.value)}
						placeholder="Search timezone..."
						className="h-6 border-0 shadow-none focus-visible:ring-0 text-xs px-0"
						onKeyDown={(e) => e.stopPropagation()}
					/>
				</div>
				<div className="max-h-60 overflow-y-auto">
					{filtered.length === 0 ? (
						<div className="px-2 py-4 text-center text-xs text-muted-foreground">
							No timezone found
						</div>
					) : (
						filtered.map((tz) => (
							<DropdownMenuItem
								key={tz}
								onSelect={() => onChange?.(tz)}
								className="text-xs"
							>
								{tz}
							</DropdownMenuItem>
						))
					)}
				</div>
			</DropdownMenuContent>
		</DropdownMenu>
	);
}
