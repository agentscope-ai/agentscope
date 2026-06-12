import { PlusCircle, Search, ShieldCheck, SlidersHorizontal, Trash } from 'lucide-react';
import { useMemo, useState, type ReactNode } from 'react';

import type { MCPClient, MCPClientStatus, PermissionContext, PermissionRule, Skill } from '@/api';
import { AddSkillDialog } from '@/components/dialog/AddSkillDialog.tsx';
import { DeleteDialog } from '@/components/dialog/DeleteDialog.tsx';
import { CreateMCPDialog } from '@/components/dialog/MCPDialog.tsx';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
	DropdownMenu,
	DropdownMenuCheckboxItem,
	DropdownMenuContent,
	DropdownMenuLabel,
	DropdownMenuSeparator,
	DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { InputGroup, InputGroupAddon, InputGroupInput } from '@/components/ui/input-group';
import { Item, ItemActions, ItemContent, ItemDescription, ItemTitle } from '@/components/ui/item';
import { Kbd, KbdGroup } from '@/components/ui/kbd';
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from '@/components/ui/resizable';
import { Separator } from '@/components/ui/separator';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { useTranslation } from '@/i18n/useI18n.ts';
import { cn } from '@/lib/utils';

interface WorkspacePanelProps {
	mcps: MCPClientStatus[];
	loading?: boolean;
	onAdd: (mcps: MCPClient[]) => Promise<void>;
	onRemove: (name: string) => Promise<void>;
	skills: Skill[];
	skillsLoading?: boolean;
	onAddSkill: (skillPath: string) => Promise<void>;
	onRemoveSkill: (name: string) => Promise<void>;
	permissionContext: PermissionContext | null;
}

type PanelId = 'permissions' | 'mcp' | 'skills';

type RuleGroup = {
	label: string;
	rules: Record<string, PermissionRule[]>;
	variant: 'default' | 'secondary' | 'destructive';
};

type VisiblePanels = Record<PanelId, boolean>;

const PANEL_VISIBILITY_KEY = 'agentscope.chat.workspacePanel.visibility';
const PANEL_LAYOUT_KEY = 'agentscope.chat.workspacePanel.layout';
const DEFAULT_VISIBLE_PANELS: VisiblePanels = {
	permissions: false,
	mcp: false,
	skills: false,
};

const PANEL_LABELS: Record<PanelId, string> = {
	permissions: 'Permissions',
	mcp: 'MCPs',
	skills: 'Skills',
};

function readVisiblePanels(): VisiblePanels {
	if (typeof window === 'undefined') return DEFAULT_VISIBLE_PANELS;

	try {
		const parsed = JSON.parse(localStorage.getItem(PANEL_VISIBILITY_KEY) ?? '{}') as Partial<
			Record<PanelId, unknown>
		>;
		return {
			permissions:
				typeof parsed.permissions === 'boolean'
					? parsed.permissions
					: DEFAULT_VISIBLE_PANELS.permissions,
			mcp: typeof parsed.mcp === 'boolean' ? parsed.mcp : DEFAULT_VISIBLE_PANELS.mcp,
			skills:
				typeof parsed.skills === 'boolean' ? parsed.skills : DEFAULT_VISIBLE_PANELS.skills,
		};
	} catch {
		return DEFAULT_VISIBLE_PANELS;
	}
}

function writeVisiblePanels(value: VisiblePanels) {
	localStorage.setItem(PANEL_VISIBILITY_KEY, JSON.stringify(value));
}

function readPanelLayout() {
	if (typeof window === 'undefined') return undefined;

	try {
		const parsed = JSON.parse(localStorage.getItem(PANEL_LAYOUT_KEY) ?? '{}') as Record<
			string,
			number
		>;
		return Object.keys(parsed).length > 0 ? parsed : undefined;
	} catch {
		return undefined;
	}
}

function writePanelLayout(layout: Record<string, number>) {
	localStorage.setItem(PANEL_LAYOUT_KEY, JSON.stringify(layout));
}

function entries<T>(value: Record<string, T> | undefined): Array<[string, T]> {
	return Object.entries(value ?? {});
}

function ruleCount(rules: Record<string, PermissionRule[]> | undefined) {
	return entries(rules).reduce((total, [, group]) => total + group.length, 0);
}

function EmptyLine({ children }: { children: string }) {
	return <p className="text-muted-foreground text-sm text-center py-4">{children}</p>;
}

function PanelShell({
	title,
	description,
	action,
	children,
}: {
	title: string;
	description?: string;
	action?: ReactNode;
	children: ReactNode;
}) {
	return (
		<section className="flex h-full min-h-0 flex-col bg-background">
			<header className="flex min-h-12 items-start justify-between gap-3 border-b px-3 py-2.5">
				<div className="min-w-0">
					<h2 className="truncate text-sm font-semibold">{title}</h2>
					{description && (
						<p className="truncate text-xs text-muted-foreground">{description}</p>
					)}
				</div>
				{action}
			</header>
			<div className="min-h-0 flex-1 overflow-y-auto p-3">{children}</div>
		</section>
	);
}

function RuleList({ group }: { group: RuleGroup }) {
	const tools = entries(group.rules);

	if (tools.length === 0) return <EmptyLine>No rules</EmptyLine>;

	return (
		<div className="flex flex-col gap-2">
			{tools.map(([toolName, rules]) => (
				<div key={`${group.label}-${toolName}`} className="rounded-md border bg-background">
					<div className="flex items-center justify-between gap-2 border-b px-3 py-2">
						<span className="truncate text-sm font-medium">{toolName}</span>
						<Badge variant={group.variant}>{rules.length}</Badge>
					</div>
					<div className="flex flex-col gap-1 p-2">
						{rules.map((rule, index) => (
							<div
								key={`${rule.source}-${rule.rule_content ?? 'tool'}-${index}`}
								className="rounded-md bg-muted/50 px-2 py-1.5"
							>
								<div className="break-all font-mono text-xs">
									{rule.rule_content ?? toolName}
								</div>
								<div className="mt-1 text-xs text-muted-foreground">
									{rule.source}
								</div>
							</div>
						))}
					</div>
				</div>
			))}
		</div>
	);
}

function PermissionsPanel({ permissionContext }: { permissionContext: PermissionContext | null }) {
	const ruleGroups: RuleGroup[] = useMemo(
		() => [
			{
				label: 'Allow',
				rules: permissionContext?.allow_rules ?? {},
				variant: 'default',
			},
			{
				label: 'Ask',
				rules: permissionContext?.ask_rules ?? {},
				variant: 'secondary',
			},
			{
				label: 'Deny',
				rules: permissionContext?.deny_rules ?? {},
				variant: 'destructive',
			},
		],
		[permissionContext],
	);
	const workingDirectories = entries(permissionContext?.working_directories);
	const totalRules = ruleGroups.reduce((total, group) => total + ruleCount(group.rules), 0);

	return (
		<PanelShell title="Permissions" description="Current permission context">
			{!permissionContext ? (
				<EmptyLine>No permission context</EmptyLine>
			) : (
				<div className="flex min-h-0 flex-col gap-3">
					<div className="rounded-md border bg-background p-3">
						<div className="flex items-center justify-between gap-2">
							<div className="flex items-center gap-2">
								<ShieldCheck className="size-4 text-muted-foreground" />
								<span className="text-sm font-medium">Mode</span>
							</div>
							<Badge variant="outline">{permissionContext.mode}</Badge>
						</div>
						<div className="mt-3 flex flex-wrap gap-2">
							<Badge variant="secondary">
								{workingDirectories.length} directories
							</Badge>
							<Badge variant="secondary">{totalRules} rules</Badge>
						</div>
					</div>

					<div className="flex flex-col gap-2">
						<h3 className="text-sm font-medium">Working directories</h3>
						{workingDirectories.length === 0 ? (
							<EmptyLine>No directories</EmptyLine>
						) : (
							<div className="flex flex-col gap-2">
								{workingDirectories.map(([key, directory]) => (
									<div key={key} className="rounded-md border bg-background p-2">
										<div className="break-all font-mono text-xs">
											{directory.path}
										</div>
										<div className="mt-1 text-xs text-muted-foreground">
											{directory.source}
										</div>
									</div>
								))}
							</div>
						)}
					</div>

					<Separator />

					{ruleGroups.map((group) => (
						<div key={group.label} className="flex flex-col gap-2">
							<div className="flex items-center justify-between gap-2">
								<h3 className="text-sm font-medium">{group.label}</h3>
								<Badge variant={group.variant}>{ruleCount(group.rules)}</Badge>
							</div>
							<RuleList group={group} />
						</div>
					))}
				</div>
			)}
		</PanelShell>
	);
}

function McpPanel({
	mcps,
	loading,
	onAdd,
	onRemoveRequest,
}: {
	mcps: MCPClientStatus[];
	loading: boolean;
	onAdd: (mcps: MCPClient[]) => Promise<void>;
	onRemoveRequest: (name: string) => void;
}) {
	const [search, setSearch] = useState('');
	const filtered = search
		? mcps.filter((mcp) => mcp.name.toLowerCase().includes(search.toLowerCase()))
		: mcps;

	return (
		<PanelShell
			title="MCPs"
			description={`${mcps.length} servers`}
			action={
				<CreateMCPDialog onAdd={onAdd}>
					<Button variant="outline" size="sm">
						<PlusCircle />
						Add
					</Button>
				</CreateMCPDialog>
			}
		>
			<div className="flex flex-col gap-2">
				<InputGroup>
					<InputGroupInput
						placeholder="Search MCP"
						value={search}
						onChange={(event) => setSearch(event.target.value)}
					/>
					<InputGroupAddon align="inline-end">
						<Search />
					</InputGroupAddon>
				</InputGroup>
				{loading ? (
					<EmptyLine>Loading...</EmptyLine>
				) : filtered.length === 0 ? (
					<EmptyLine>No MCPs found</EmptyLine>
				) : (
					filtered.map((mcp) => (
						<Item key={mcp.name} variant="outline">
							<ItemContent>
								<ItemTitle className="flex items-center gap-x-2">
									<span
										className={cn(
											'size-2 shrink-0 rounded-full',
											mcp.is_healthy ? 'bg-green-500' : 'bg-red-500',
										)}
									/>
									{mcp.name}
								</ItemTitle>
								<ItemDescription>
									<KbdGroup>
										<Kbd>
											{mcp.mcp_config.type === 'stdio_mcp' ? 'STDIO' : 'HTTP'}
										</Kbd>
										<Kbd>{mcp.tools.length} tools</Kbd>
									</KbdGroup>
								</ItemDescription>
							</ItemContent>
							<ItemActions>
								<Button
									variant="outline"
									size="icon-sm"
									onClick={() => onRemoveRequest(mcp.name)}
									aria-label={`Remove ${mcp.name}`}
								>
									<Trash />
								</Button>
							</ItemActions>
						</Item>
					))
				)}
			</div>
		</PanelShell>
	);
}

function SkillsPanel({
	skills,
	loading,
	onAddSkill,
	onRemoveRequest,
}: {
	skills: Skill[];
	loading: boolean;
	onAddSkill: (skillPath: string) => Promise<void>;
	onRemoveRequest: (name: string) => void;
}) {
	const [search, setSearch] = useState('');
	const filtered = search
		? skills.filter((skill) => skill.name.toLowerCase().includes(search.toLowerCase()))
		: skills;

	return (
		<PanelShell
			title="Skills"
			description={`${skills.length} skills`}
			action={
				<AddSkillDialog onAdd={onAddSkill}>
					<Button variant="outline" size="sm">
						<PlusCircle />
						Add
					</Button>
				</AddSkillDialog>
			}
		>
			<div className="flex flex-col gap-2">
				<InputGroup>
					<InputGroupInput
						placeholder="Search skills"
						value={search}
						onChange={(event) => setSearch(event.target.value)}
					/>
					<InputGroupAddon align="inline-end">
						<Search />
					</InputGroupAddon>
				</InputGroup>
				{loading ? (
					<EmptyLine>Loading...</EmptyLine>
				) : filtered.length === 0 ? (
					<EmptyLine>No skills found</EmptyLine>
				) : (
					filtered.map((skill) => (
						<Item key={skill.name} variant="outline">
							<ItemContent>
								<ItemTitle>{skill.name}</ItemTitle>
								<ItemDescription>{skill.description}</ItemDescription>
							</ItemContent>
							<ItemActions>
								<Button
									variant="outline"
									size="icon-sm"
									onClick={() => onRemoveRequest(skill.name)}
									aria-label={`Remove ${skill.name}`}
								>
									<Trash />
								</Button>
							</ItemActions>
						</Item>
					))
				)}
			</div>
		</PanelShell>
	);
}

export function WorkspacePanel({
	mcps,
	loading = false,
	onAdd,
	onRemove,
	skills,
	skillsLoading = false,
	onAddSkill,
	onRemoveSkill,
	permissionContext,
}: WorkspacePanelProps) {
	const { t } = useTranslation();
	const [visiblePanels, setVisiblePanels] = useState<VisiblePanels>(readVisiblePanels);
	const [deleteOpen, setDeleteOpen] = useState(false);
	const [deleteTarget, setDeleteTarget] = useState<string | null>(null);
	const [skillDeleteOpen, setSkillDeleteOpen] = useState(false);
	const [skillDeleteTarget, setSkillDeleteTarget] = useState<string | null>(null);
	const [defaultLayout] = useState(readPanelLayout);

	const selectedPanels = (Object.keys(PANEL_LABELS) as PanelId[]).filter(
		(id) => visiblePanels[id],
	);
	const hasVisiblePanels = selectedPanels.length > 0;

	const togglePanel = (panelId: PanelId, checked: boolean) => {
		const next = { ...visiblePanels, [panelId]: checked };
		setVisiblePanels(next);
		writeVisiblePanels(next);
	};

	return (
		<TooltipProvider>
			<div className="flex h-full shrink-0 border-l bg-background">
				<div className="flex w-12 flex-col items-center gap-2 p-2">
					<DropdownMenu>
						<Tooltip>
							<TooltipTrigger asChild>
								<DropdownMenuTrigger asChild>
									<Button
										size="icon-sm"
										variant={hasVisiblePanels ? 'secondary' : 'ghost'}
										aria-label="Configure session panels"
									>
										<SlidersHorizontal />
									</Button>
								</DropdownMenuTrigger>
							</TooltipTrigger>
							<TooltipContent side="left">Session panels</TooltipContent>
						</Tooltip>
						<DropdownMenuContent align="end" side="left" className="w-48">
							<DropdownMenuLabel>Session panels</DropdownMenuLabel>
							<DropdownMenuSeparator />
							{(Object.keys(PANEL_LABELS) as PanelId[]).map((panelId) => (
								<DropdownMenuCheckboxItem
									key={panelId}
									checked={visiblePanels[panelId]}
									onCheckedChange={(checked) =>
										togglePanel(panelId, checked === true)
									}
									onSelect={(event) => event.preventDefault()}
								>
									{PANEL_LABELS[panelId]}
								</DropdownMenuCheckboxItem>
							))}
							<DropdownMenuCheckboxItem disabled checked={false}>
								Background Tasks
							</DropdownMenuCheckboxItem>
						</DropdownMenuContent>
					</DropdownMenu>
				</div>

				<aside
					className={cn(
						'flex h-full min-h-0 overflow-hidden transition-[width,opacity] duration-200',
						hasVisiblePanels ? 'w-[min(72vw,72rem)] opacity-100' : 'w-0 opacity-0',
					)}
					aria-hidden={!hasVisiblePanels}
				>
					{hasVisiblePanels && (
						<div className="min-h-0 flex-1">
							<ResizablePanelGroup
								orientation="horizontal"
								defaultLayout={defaultLayout}
								onLayoutChanged={writePanelLayout}
								className="h-full"
							>
								{selectedPanels.flatMap((panelId, index) => [
									index > 0 ? (
										<ResizableHandle key={`${panelId}-handle`} withHandle />
									) : null,
									<ResizablePanel key={panelId} id={panelId} minSize="18%">
										{panelId === 'permissions' && (
											<PermissionsPanel
												permissionContext={permissionContext}
											/>
										)}
										{panelId === 'mcp' && (
											<McpPanel
												mcps={mcps}
												loading={loading}
												onAdd={onAdd}
												onRemoveRequest={(name) => {
													setDeleteTarget(name);
													setDeleteOpen(true);
												}}
											/>
										)}
										{panelId === 'skills' && (
											<SkillsPanel
												skills={skills}
												loading={skillsLoading}
												onAddSkill={onAddSkill}
												onRemoveRequest={(name) => {
													setSkillDeleteTarget(name);
													setSkillDeleteOpen(true);
												}}
											/>
										)}
									</ResizablePanel>,
								])}
							</ResizablePanelGroup>
						</div>
					)}
				</aside>
			</div>

			<DeleteDialog
				open={deleteOpen}
				onOpenChange={setDeleteOpen}
				title={t('common.deleteTitle', {
					entity: t('dialog-mcp-delete.entity'),
					name: deleteTarget ?? '',
				})}
				description={t('common.deleteDescription')}
				onConfirm={async () => {
					if (deleteTarget) await onRemove(deleteTarget);
				}}
			/>
			<DeleteDialog
				open={skillDeleteOpen}
				onOpenChange={setSkillDeleteOpen}
				title={t('common.deleteTitle', {
					entity: t('dialog-mcp-delete.skillEntity'),
					name: skillDeleteTarget ?? '',
				})}
				description={t('dialog-mcp-delete.skillDescription')}
				onConfirm={async () => {
					if (skillDeleteTarget) await onRemoveSkill(skillDeleteTarget);
				}}
			/>
		</TooltipProvider>
	);
}
