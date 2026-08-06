import { Check, ChevronDown, Plus, Trash2 } from 'lucide-react';

import type { AgentView, ChannelBinding, SessionScope } from '@/api';
import { AgentSelect } from '@/components/select/AgentSelect';
import { Button } from '@/components/ui/button';
import {
	DropdownMenu,
	DropdownMenuContent,
	DropdownMenuItem,
	DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Input } from '@/components/ui/input';
import { useTranslation } from '@/i18n/useI18n';

const SESSION_SCOPES: SessionScope[] = ['per_chat', 'per_chat_user'];

/** Session-scope picker on the shadcn dropdown-menu, matching AgentSelect. */
function ScopeSelect({
	value,
	onChange,
}: {
	value: SessionScope;
	onChange: (scope: SessionScope) => void;
}) {
	const { t } = useTranslation();
	return (
		<DropdownMenu>
			<DropdownMenuTrigger asChild>
				<Button
					variant="outline"
					size="default"
					className="w-full justify-between gap-1 font-normal"
				>
					<span className="truncate">{t(`channel.sessionScope.${value}`)}</span>
					<ChevronDown className="size-3.5 text-muted-foreground" />
				</Button>
			</DropdownMenuTrigger>
			<DropdownMenuContent align="start" className="min-w-40">
				{SESSION_SCOPES.map((s) => (
					<DropdownMenuItem key={s} onSelect={() => onChange(s)}>
						<Check
							className={`size-3.5 shrink-0 ${s === value ? 'opacity-100' : 'opacity-0'}`}
						/>
						<span className="flex-1">{t(`channel.sessionScope.${s}`)}</span>
					</DropdownMenuItem>
				))}
			</DropdownMenuContent>
		</DropdownMenu>
	);
}

interface Props {
	value: ChannelBinding[];
	onChange: (bindings: ChannelBinding[]) => void;
	agents: AgentView[];
}

/**
 * Editor for a channel's routing rules. The last row is always the
 * catch-all (match_value === '*'); extra rules are matched, in order,
 * before it. First match wins.
 */
export function BindingsEditor({ value, onChange, agents }: Props) {
	const { t } = useTranslation();

	const update = (i: number, patch: Partial<ChannelBinding>) => {
		onChange(value.map((b, idx) => (idx === i ? { ...b, ...patch } : b)));
	};

	const removeRule = (i: number) => {
		onChange(value.filter((_, idx) => idx !== i));
	};

	const addRule = () => {
		const catchAll = value[value.length - 1];
		const rule: ChannelBinding = {
			match_key: 'chat_id',
			match_value: '',
			agent_id: catchAll?.agent_id ?? agents[0]?.id ?? '',
			session_scope: 'per_chat',
		};
		// Insert before the catch-all so it stays last.
		onChange([...value.slice(0, -1), rule, ...value.slice(-1)]);
	};

	// Agent + session-scope selects, shared by every rule and the fallback.
	const targets = (binding: ChannelBinding, i: number) => (
		<div className="grid grid-cols-2 gap-2.5">
			<div className="flex flex-col gap-1">
				<span className="text-xs text-muted-foreground">
					{t('channel.binding.routeTo')}
				</span>
				<AgentSelect
					size="default"
					className="w-full"
					agents={agents}
					value={binding.agent_id || null}
					onChange={(id) => update(i, { agent_id: id })}
				/>
			</div>
			<div className="flex flex-col gap-1">
				<span className="text-xs text-muted-foreground">{t('channel.binding.scope')}</span>
				<ScopeSelect
					value={binding.session_scope}
					onChange={(s) => update(i, { session_scope: s })}
				/>
			</div>
		</div>
	);

	return (
		<div className="flex flex-col gap-2.5">
			{value.map((binding, i) => {
				const isCatchAll = i === value.length - 1;
				if (isCatchAll) {
					return (
						<div
							key={i}
							className="flex flex-col gap-2.5 rounded-lg border border-dashed bg-muted/30 p-3"
						>
							<div>
								<span className="text-xs font-medium">
									{t('channel.binding.fallback')}
								</span>
								<p className="mt-0.5 text-xs text-muted-foreground">
									{t('channel.binding.fallbackDesc')}
								</p>
							</div>
							{targets(binding, i)}
						</div>
					);
				}
				return (
					<div key={i} className="flex flex-col gap-2.5 rounded-lg border p-3">
						<div className="flex items-center justify-between">
							<span className="text-xs font-medium text-muted-foreground">
								{t('channel.binding.rule')} {i + 1}
							</span>
							<Button
								size="icon-sm"
								variant="ghost"
								className="-mr-1 size-6 text-destructive"
								onClick={() => removeRule(i)}
							>
								<Trash2 className="size-3.5" />
							</Button>
						</div>
						<div className="grid grid-cols-2 gap-2.5">
							<div className="flex flex-col gap-1">
								<span className="text-xs text-muted-foreground">
									{t('channel.binding.matchKey')}
								</span>
								<Input
									className="font-mono text-xs"
									value={binding.match_key}
									onChange={(e) => update(i, { match_key: e.target.value })}
									placeholder="chat_id"
								/>
							</div>
							<div className="flex flex-col gap-1">
								<span className="text-xs text-muted-foreground">
									{t('channel.binding.matchValue')}
								</span>
								<Input
									className="font-mono text-xs"
									value={binding.match_value}
									onChange={(e) => update(i, { match_value: e.target.value })}
									placeholder={t('channel.binding.matchValuePlaceholder')}
								/>
							</div>
						</div>
						{targets(binding, i)}
					</div>
				);
			})}

			<Button variant="ghost" size="sm" className="self-start" onClick={addRule}>
				<Plus className="size-3.5" />
				{t('channel.binding.addRule')}
			</Button>
		</div>
	);
}
