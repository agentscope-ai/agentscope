import { CircleAlert, PlusCircle, Search, SearchX, Store } from 'lucide-react';
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';

import type { MCPView, SkillView } from '@/api';
import { Alert, AlertDescription } from '@/components/ui/alert.tsx';
import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar.tsx';
import { Button } from '@/components/ui/button.tsx';
import { Checkbox } from '@/components/ui/checkbox.tsx';
import {
	Empty,
	EmptyContent,
	EmptyDescription,
	EmptyHeader,
	EmptyMedia,
	EmptyTitle,
} from '@/components/ui/empty.tsx';
import { InputGroup, InputGroupAddon, InputGroupInput } from '@/components/ui/input-group';
import {
	Item,
	ItemContent,
	ItemDescription,
	ItemGroup,
	ItemMedia,
	ItemTitle,
} from '@/components/ui/item.tsx';
import { Spinner } from '@/components/ui/spinner.tsx';
import { useMCPs } from '@/hooks/useMCPs.ts';
import { useSkills } from '@/hooks/useSkills.ts';
import { useTranslation } from '@/i18n/useI18n.ts';

export interface AgentBindings {
	mcp_ids: string[];
	skill_ids: string[];
}

/** Which of the two lists to render. Doubles as the key into `values`. */
export type AgentBindingKind = keyof AgentBindings;

interface Props {
	kind: AgentBindingKind;
	values: AgentBindings;
	onChange: (next: AgentBindings) => void;
	/**
	 * Whether this agent already has a workspace, in which case editing
	 * the list changes nothing about it — seeding happens once, at
	 * creation. Left `false` on the create form, where it cannot apply.
	 */
	hasWorkspace?: boolean;
}

/**
 * One list of what the agent comes with, picked from the user's library.
 *
 * Hand-written rather than schema-driven: `mcp_ids` / `skill_ids` are
 * lists of opaque ids that have to be picked, not typed, so they are
 * marked `SkipJsonSchema` on the server and never reach `AgentFormFields`.
 *
 * Both hooks run whichever kind is shown — they are the user's own
 * library, cheap, and prefetching the hidden tab means switching to it
 * does not flash a spinner.
 */
export function AgentBindingFields({ kind, values, onChange, hasWorkspace = false }: Props) {
	const { t } = useTranslation();
	const { mcps, loading: mcpsLoading } = useMCPs();
	const { skills, loading: skillsLoading } = useSkills();

	const navigate = useNavigate();
	const [search, setSearch] = useState('');

	const isMcp = kind === 'mcp_ids';
	const i18n = isMcp ? 'mcps' : 'skills';
	const loading = isMcp ? mcpsLoading : skillsLoading;
	const items: Array<MCPView | SkillView> = isMcp ? mcps : skills;
	const picked = values[kind];

	const needle = search.trim().toLowerCase();
	const shown = needle
		? items.filter((item) =>
				[item.name, item.display_name ?? '', item.description, ...item.tags].some((field) =>
					field.toLowerCase().includes(needle),
				),
			)
		: items;

	const toggle = (id: string) => {
		onChange({
			...values,
			[kind]: picked.includes(id) ? picked.filter((i) => i !== id) : [...picked, id],
		});
	};

	return (
		<div className="flex flex-col gap-y-3">
			<p className="text-muted-foreground text-sm">
				{t(`agent-form.bindings.${i18n}.description`)}
			</p>
			{hasWorkspace && (
				<Alert>
					<CircleAlert />
					<AlertDescription>{t('agent-form.bindings.alreadySeeded')}</AlertDescription>
				</Alert>
			)}
			{/* Leading icon, matching the hub pages these lists are picked
			    from. */}
			<InputGroup>
				<InputGroupAddon align="inline-start">
					<Search />
				</InputGroupAddon>
				<InputGroupInput
					placeholder={t(`agent-form.bindings.${i18n}.searchPlaceholder`)}
					value={search}
					onChange={(e) => setSearch(e.target.value)}
				/>
			</InputGroup>
			{loading ? (
				<div className="flex justify-center py-10">
					<Spinner />
				</div>
			) : shown.length === 0 ? (
				<Empty className="border-none py-10">
					<EmptyHeader>
						<EmptyMedia variant="icon">
							{needle ? <SearchX /> : <PlusCircle />}
						</EmptyMedia>
						<EmptyTitle>
							{needle
								? t('panel.search.emptyTitle')
								: t(`agent-form.bindings.${i18n}.emptyTitle`)}
						</EmptyTitle>
						<EmptyDescription>
							{needle
								? t('panel.search.emptyDescription', { query: search })
								: t(`agent-form.bindings.${i18n}.empty`)}
						</EmptyDescription>
					</EmptyHeader>
					{/* Only offered when the library is genuinely empty — a
					    search that found nothing is no reason to leave. */}
					{!needle && (
						<EmptyContent>
							<Button
								variant="outline"
								size="sm"
								onClick={() => navigate(isMcp ? '/mcp' : '/skill')}
							>
								<Store />
								{t(`agent-form.bindings.${i18n}.browseHub`)}
							</Button>
						</EmptyContent>
					)}
				</Empty>
			) : (
				<ItemGroup className="gap-1">
					{shown.map((item) => (
						<Item key={item.id}>
							<Checkbox
								checked={picked.includes(item.id)}
								onCheckedChange={() => toggle(item.id)}
							/>
							<ItemMedia>
								<Avatar className="rounded-md">
									<AvatarImage
										src={item.icon_url ?? undefined}
										alt={item.display_name || item.name}
										loading="lazy"
									/>
									<AvatarFallback className="rounded-md">
										{(item.display_name || item.name).slice(0, 1).toUpperCase()}
									</AvatarFallback>
								</Avatar>
							</ItemMedia>
							<ItemContent>
								{/* Name, author, tags — each step lighter than
								    the last, so the eye lands on the name
								    first. */}
								<ItemTitle>
									<span className="font-medium">
										{item.display_name || item.name}
									</span>
									{item.author && (
										<span className="text-xs text-muted-foreground">
											@{item.author}
										</span>
									)}
									{item.tags.slice(0, 4).map((tag) => (
										<span
											key={tag}
											className="text-xs text-muted-foreground/60"
										>
											#{tag}
										</span>
									))}
								</ItemTitle>
								<ItemDescription className="line-clamp-1">
									{item.description}
								</ItemDescription>
							</ItemContent>
						</Item>
					))}
				</ItemGroup>
			)}
		</div>
	);
}
