import {
	PanelRightClose,
	PanelRightOpen,
	PlusCircle,
	Search,
	ShieldCheck,
	Trash,
} from 'lucide-react';
import { useMemo, useState } from 'react';

import type { MCPClient, MCPClientStatus, PermissionContext, PermissionRule, Skill } from '@/api';
import { AddSkillDialog } from '@/components/dialog/AddSkillDialog.tsx';
import { DeleteDialog } from '@/components/dialog/DeleteDialog.tsx';
import { CreateMCPDialog } from '@/components/dialog/MCPDialog.tsx';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { InputGroup, InputGroupAddon, InputGroupInput } from '@/components/ui/input-group';
import { Item, ItemActions, ItemContent, ItemDescription, ItemTitle } from '@/components/ui/item';
import { Kbd, KbdGroup } from '@/components/ui/kbd';
import { Separator } from '@/components/ui/separator';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs.tsx';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { useTranslation } from '@/i18n/useI18n.ts';
import { cn } from '@/lib/utils';

interface WorkspacePanelProps {
	open: boolean;
	onOpenChange: (open: boolean) => void;
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

type RuleGroup = {
	label: string;
	rules: Record<string, PermissionRule[]>;
	variant: 'default' | 'secondary' | 'destructive';
};

function entries<T>(value: Record<string, T> | undefined): Array<[string, T]> {
	return Object.entries(value ?? {});
}

function ruleCount(rules: Record<string, PermissionRule[]> | undefined) {
	return entries(rules).reduce((total, [, group]) => total + group.length, 0);
}

function EmptyLine({ children }: { children: string }) {
	return <p className="text-muted-foreground text-sm text-center py-4">{children}</p>;
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

function PermissionPanel({ permissionContext }: { permissionContext: PermissionContext | null }) {
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

	if (!permissionContext) {
		return <EmptyLine>No permission context</EmptyLine>;
	}

	return (
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
					<Badge variant="secondary">{workingDirectories.length} directories</Badge>
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
								<div className="break-all font-mono text-xs">{directory.path}</div>
								<div className="mt-1 text-xs text-muted-foreground">
									{directory.source}
								</div>
							</div>
						))}
					</div>
				)}
			</div>

			<Separator />

			<Tabs defaultValue="allow" className="min-h-0">
				<TabsList className="grid w-full grid-cols-3">
					{ruleGroups.map((group) => (
						<TabsTrigger key={group.label} value={group.label.toLowerCase()}>
							{group.label}
						</TabsTrigger>
					))}
				</TabsList>
				{ruleGroups.map((group) => (
					<TabsContent
						key={group.label}
						value={group.label.toLowerCase()}
						className="min-h-0 overflow-y-auto"
					>
						<RuleList group={group} />
					</TabsContent>
				))}
			</Tabs>
		</div>
	);
}

export function WorkspacePanel({
	open,
	onOpenChange,
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
	const [search, setSearch] = useState('');
	const [skillSearch, setSkillSearch] = useState('');
	const [activeTab, setActiveTab] = useState('workspace');
	const [deleteOpen, setDeleteOpen] = useState(false);
	const [deleteTarget, setDeleteTarget] = useState<string | null>(null);
	const [skillDeleteOpen, setSkillDeleteOpen] = useState(false);
	const [skillDeleteTarget, setSkillDeleteTarget] = useState<string | null>(null);

	const filtered = search
		? mcps.filter((m) => m.name.toLowerCase().includes(search.toLowerCase()))
		: mcps;

	const filteredSkills = skillSearch
		? skills.filter((s) => s.name.toLowerCase().includes(skillSearch.toLowerCase()))
		: skills;

	return (
		<TooltipProvider>
			<div className="flex h-full shrink-0 border-l bg-background">
				<div className="flex w-12 flex-col items-center gap-2 p-2">
					<Tooltip>
						<TooltipTrigger asChild>
							<Button
								size="icon-sm"
								variant={open ? 'secondary' : 'ghost'}
								onClick={() => onOpenChange(!open)}
								aria-controls="workspace-context-panel"
								aria-expanded={open}
								aria-label={open ? 'Close workspace panel' : 'Open workspace panel'}
							>
								{open ? <PanelRightClose /> : <PanelRightOpen />}
							</Button>
						</TooltipTrigger>
						<TooltipContent side="left">
							{open ? 'Close workspace panel' : 'Open workspace panel'}
						</TooltipContent>
					</Tooltip>
				</div>

				<aside
					id="workspace-context-panel"
					className={cn(
						'flex h-full min-h-0 flex-col overflow-hidden transition-[width,opacity] duration-200',
						open ? 'w-88 opacity-100' : 'w-0 opacity-0',
					)}
					aria-hidden={!open}
				>
					{open && (
						<div className="flex min-h-0 w-88 flex-1 flex-col gap-3 p-3">
							<div className="flex items-center justify-between gap-2">
								<div>
									<h2 className="text-sm font-semibold">Session context</h2>
									<p className="text-xs text-muted-foreground">
										Workspace and permissions
									</p>
								</div>
							</div>

							<Tabs
								value={activeTab}
								onValueChange={setActiveTab}
								className="min-h-0 flex-1"
							>
								<TabsList className="grid w-full grid-cols-2">
									<TabsTrigger value="workspace">Workspace</TabsTrigger>
									<TabsTrigger value="permissions">Permissions</TabsTrigger>
								</TabsList>

								<TabsContent value="workspace" className="min-h-0 overflow-y-auto">
									<Tabs defaultValue="mcp" className="min-h-0">
										<TabsList className="grid w-full grid-cols-2">
											<TabsTrigger value="mcp">MCP</TabsTrigger>
											<TabsTrigger value="skill">Skill</TabsTrigger>
										</TabsList>
										<TabsContent value="mcp" asChild>
											<div className="flex flex-col gap-2">
												<InputGroup>
													<InputGroupInput
														placeholder="Search MCP"
														value={search}
														onChange={(e) => setSearch(e.target.value)}
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
																			mcp.is_healthy
																				? 'bg-green-500'
																				: 'bg-red-500',
																		)}
																	/>
																	{mcp.name}
																</ItemTitle>
																<ItemDescription>
																	<KbdGroup>
																		<Kbd>
																			{mcp.mcp_config.type ===
																			'stdio_mcp'
																				? 'STDIO'
																				: 'HTTP'}
																		</Kbd>
																		<Kbd>
																			{mcp.tools.length} tools
																		</Kbd>
																	</KbdGroup>
																</ItemDescription>
															</ItemContent>
															<ItemActions>
																<Button
																	variant="outline"
																	size="icon-sm"
																	onClick={() => {
																		setDeleteTarget(mcp.name);
																		setDeleteOpen(true);
																	}}
																	aria-label={`Remove ${mcp.name}`}
																>
																	<Trash />
																</Button>
															</ItemActions>
														</Item>
													))
												)}
											</div>
										</TabsContent>
										<TabsContent value="skill" asChild>
											<div className="flex flex-col gap-2">
												<InputGroup>
													<InputGroupInput
														placeholder="Search skills"
														value={skillSearch}
														onChange={(e) =>
															setSkillSearch(e.target.value)
														}
													/>
													<InputGroupAddon align="inline-end">
														<Search />
													</InputGroupAddon>
												</InputGroup>
												{skillsLoading ? (
													<EmptyLine>Loading...</EmptyLine>
												) : filteredSkills.length === 0 ? (
													<EmptyLine>No skills found</EmptyLine>
												) : (
													filteredSkills.map((skill) => (
														<Item key={skill.name} variant="outline">
															<ItemContent>
																<ItemTitle>{skill.name}</ItemTitle>
																<ItemDescription>
																	{skill.description}
																</ItemDescription>
															</ItemContent>
															<ItemActions>
																<Button
																	variant="outline"
																	size="icon-sm"
																	onClick={() => {
																		setSkillDeleteTarget(
																			skill.name,
																		);
																		setSkillDeleteOpen(true);
																	}}
																	aria-label={`Remove ${skill.name}`}
																>
																	<Trash />
																</Button>
															</ItemActions>
														</Item>
													))
												)}
											</div>
										</TabsContent>
									</Tabs>
								</TabsContent>

								<TabsContent
									value="permissions"
									className="min-h-0 overflow-y-auto"
								>
									<PermissionPanel permissionContext={permissionContext} />
								</TabsContent>
							</Tabs>

							{activeTab === 'workspace' && (
								<div className="flex gap-2">
									<CreateMCPDialog onAdd={onAdd}>
										<Button variant="outline" className="flex-1">
											<PlusCircle />
											Add MCP
										</Button>
									</CreateMCPDialog>
									<AddSkillDialog onAdd={onAddSkill}>
										<Button variant="outline" className="flex-1">
											<PlusCircle />
											Add Skill
										</Button>
									</AddSkillDialog>
								</div>
							)}
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
