import { Search } from 'lucide-react';

import { Input } from '@/components/ui/input.tsx';
import { Item, ItemContent, ItemDescription, ItemMedia, ItemTitle } from '@/components/ui/item.tsx';
import { Separator } from '@/components/ui/separator.tsx';

interface Props {
	title: string;
	description?: string;
	/** Shown beside the title — a hub's icon, or a lucide glyph for "mine". */
	icon?: React.ReactNode;
	/** Renders a search box under the header when given. */
	search?: { value: string; onChange: (value: string) => void; placeholder: string };
	children: React.ReactNode;
}

/**
 * The frame every MCP / skill hub panel sits in: a title, an optional search
 * box, and a body.
 *
 * Only the body scrolls — the header and search box stay put, so the search
 * box you are typing into cannot slide away under a long result list. The
 * separator sits outside the padded header so it spans the full panel
 * width, matching the credential detail panel.
 */
export function ResourcePanel({ title, description, icon, search, children }: Props) {
	return (
		<div className="flex h-full flex-col">
			<div className="flex shrink-0 flex-col gap-y-4 p-[18px_18px_16px]">
				{/* Borderless Item, so the header lines up with the rows
				    below it rather than being a second layout. */}
				<Item className="p-0">
					{icon && <ItemMedia>{icon}</ItemMedia>}
					<ItemContent>
						<ItemTitle className="text-lg">{title}</ItemTitle>
						{description ? <ItemDescription>{description}</ItemDescription> : null}
					</ItemContent>
				</Item>
				{search && (
					<div className="relative">
						<Search className="absolute left-3 top-1/2 -translate-y-1/2 size-3.5 text-muted-foreground" />
						<Input
							className="border-0 bg-secondary pl-8 shadow-none"
							placeholder={search.placeholder}
							value={search.value}
							onChange={(e) => search.onChange(e.target.value)}
						/>
					</div>
				)}
			</div>

			<Separator className="shrink-0" />

			{/* min-h-0 so the flex child may shrink below its content height —
			    without it the body grows and the whole page scrolls instead.
			    scroll-fade dissolves both edges as you scroll, so cards do
			    not butt up hard against the search box or the panel bottom;
			    with no overflow it shows nothing. No horizontal padding: the
			    rows own it, so their active border-left reaches the edge. */}
			<div className="flex-1 min-h-0 overflow-y-auto scroll-fade">{children}</div>
		</div>
	);
}
