import type { PermissionContext, PermissionRule } from '@agentscope-ai/agentscope/permission';
import { Ban, CircleHelp, FolderOpen, ShieldCheck, ShieldX } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';

import { PanelEmpty } from '@/components/panel/PanelEmpty';
import { Badge } from '@/components/ui/badge';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { useTranslation } from '@/i18n/useI18n';

/** The behavior keys we render sections for (excludes ``passthrough``). */
type RuleBehavior = 'allow' | 'deny' | 'ask';

interface PermissionPanelProps {
	/**
	 * The permission context to render. Pass ``null`` when no data is
	 * available yet (renders an empty state).
	 */
	permissionContext: PermissionContext | null;
}

/** i18n key suffix for each behavior group title. */
const BEHAVIOR_META: Record<RuleBehavior, { i18nKey: string; icon: typeof ShieldCheck }> = {
	allow: { i18nKey: 'panel.permission.allow', icon: ShieldCheck },
	deny: { i18nKey: 'panel.permission.deny', icon: Ban },
	ask: { i18nKey: 'panel.permission.ask', icon: CircleHelp },
};

/**
 * A monospace value (a path or rule pattern) that truncates to fit its
 * row. When (and only when) the text is actually clipped, hovering
 * reveals the full value in a tooltip anchored to the left.
 *
 * @param value - The text to display.
 * @returns The truncating value element, with a tooltip when clipped.
 */
function TruncatedCode({ value }: { value: string }) {
	const ref = useRef<HTMLSpanElement>(null);
	const [truncated, setTruncated] = useState(false);

	useEffect(() => {
		const el = ref.current;
		if (!el) return;
		const check = () => setTruncated(el.scrollWidth > el.clientWidth);
		check();
		const observer = new ResizeObserver(check);
		observer.observe(el);
		return () => observer.disconnect();
	}, [value]);

	return (
		<Tooltip>
			<TooltipTrigger asChild>
				<span ref={ref} className="min-w-0 flex-1 truncate text-left font-mono text-xs">
					{value}
				</span>
			</TooltipTrigger>
			{truncated ? (
				<TooltipContent side="left" className="max-w-sm font-mono break-all">
					{value}
				</TooltipContent>
			) : null}
		</Tooltip>
	);
}

/**
 * A single tool's rule card: a header naming the tool plus one row per
 * matching rule (pattern on the left, source on the right).
 *
 * @param toolName - The tool these rules apply to.
 * @param rules - The rules configured for this tool under one behavior.
 * @returns The tool card element.
 */
function ToolRuleCard({ toolName, rules }: { toolName: string; rules: PermissionRule[] }) {
	const { t } = useTranslation();
	return (
		<div className="rounded-md border">
			<div className="flex items-center gap-x-2 border-b px-2 py-1.5 text-sm font-medium">
				{toolName}
				<Badge variant="secondary" className="ml-auto">
					{rules.length}
				</Badge>
			</div>
			<ul className="flex flex-col">
				{rules.map((rule, index) => (
					<li
						key={`${rule.rule_content ?? '*'}-${index}`}
						className="flex items-center justify-between gap-x-2 px-2 py-1.5 text-xs not-last:border-b"
					>
						{rule.rule_content ? (
							<TruncatedCode value={rule.rule_content} />
						) : (
							<span className="min-w-0 flex-1 text-muted-foreground">
								{t('panel.permission.anyInvocation')}
							</span>
						)}
						<Badge variant="outline" className="shrink-0">
							{rule.source}
						</Badge>
					</li>
				))}
			</ul>
		</div>
	);
}

/**
 * One behavior group (allow / deny / ask): a titled section that lists
 * a {@link ToolRuleCard} per tool that has rules under this behavior.
 *
 * @param behavior - The behavior category for this section.
 * @param ruleMap - Rules keyed by tool name for this behavior.
 * @returns The section element, or ``null`` when there are no rules.
 */
function BehaviorGroup({
	behavior,
	ruleMap,
}: {
	behavior: RuleBehavior;
	ruleMap: Record<string, PermissionRule[]> | undefined;
}) {
	const { t } = useTranslation();
	const entries = Object.entries(ruleMap ?? {}).filter(([, rules]) => rules.length > 0);
	if (entries.length === 0) return null;

	const meta = BEHAVIOR_META[behavior];
	const Icon = meta.icon;

	return (
		<div className="flex flex-col gap-y-1.5">
			<div className="flex items-center gap-x-1.5 text-xs font-medium text-muted-foreground">
				<Icon className="size-3.5" />
				{t(meta.i18nKey)}
			</div>
			{entries.map(([toolName, rules]) => (
				<ToolRuleCard key={toolName} toolName={toolName} rules={rules} />
			))}
		</div>
	);
}

/**
 * Pure content body for the Permission dock panel. Shows the active
 * permission mode, the working directories in scope, and the configured
 * rules grouped by behavior (deny / ask / allow) and then by tool. Data
 * arrives via props so it owns no data fetching.
 *
 * Renders without its own header/border — the surrounding `Panel`
 * chrome (from `PanelDock`) provides those.
 *
 * @param permissionContext - The permission context, or ``null``.
 * @returns The permission panel body.
 */
export function PermissionPanel({ permissionContext }: PermissionPanelProps) {
	const { t } = useTranslation();
	const workingDirs = Object.values(permissionContext?.working_directories ?? {});
	const hasRules =
		Object.keys(permissionContext?.allow_rules ?? {}).length > 0 ||
		Object.keys(permissionContext?.deny_rules ?? {}).length > 0 ||
		Object.keys(permissionContext?.ask_rules ?? {}).length > 0;

	return (
		<div className="flex flex-col flex-1 min-h-0 gap-y-3">
			<span className="text-muted-foreground text-sm">
				{t('panel.permission.description')}
			</span>

			<div className="flex flex-col flex-1 min-h-0 overflow-y-auto gap-y-4">
				{/* Working directories — always shown, with an empty state. */}
				<div className="flex flex-col gap-y-1.5">
					<div className="flex items-center gap-x-1.5 text-xs font-medium text-muted-foreground">
						<FolderOpen className="size-3.5" />
						{t('panel.permission.workingDirectories')}
					</div>
					{workingDirs.length === 0 ? (
						<p className="text-muted-foreground text-xs px-1 py-2">
							{t('panel.permission.noWorkingDirectories')}
						</p>
					) : (
						<ul className="flex flex-col rounded-md border">
							{workingDirs.map((dir) => (
								<li
									key={dir.path}
									className="flex items-center justify-between gap-x-2 px-2 py-1.5 text-xs not-last:border-b"
								>
									<TruncatedCode value={dir.path} />
									<Badge variant="outline" className="shrink-0">
										{dir.source}
									</Badge>
								</li>
							))}
						</ul>
					)}
				</div>

				{/* Rules grouped by behavior, then by tool. */}
				{hasRules ? (
					<>
						<BehaviorGroup behavior="deny" ruleMap={permissionContext?.deny_rules} />
						<BehaviorGroup behavior="ask" ruleMap={permissionContext?.ask_rules} />
						<BehaviorGroup behavior="allow" ruleMap={permissionContext?.allow_rules} />
					</>
				) : (
					<PanelEmpty
						icon={ShieldX}
						title={t('panel.permission.emptyTitle')}
						description={t('panel.permission.emptyDescription')}
					/>
				)}
			</div>
		</div>
	);
}
