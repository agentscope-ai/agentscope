import { FileX, PlusCircle, Search, SearchX, Trash } from 'lucide-react';
import { useState } from 'react';

import type { Skill } from '@/api';
import type { UploadOptions } from '@/api/workspace';
import { AddSkillDialog } from '@/components/dialog/AddSkillDialog.tsx';
import { DeleteDialog } from '@/components/dialog/DeleteDialog.tsx';
import { PanelEmpty } from '@/components/panel/PanelEmpty';
import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar.tsx';
import { Button } from '@/components/ui/button';
import { InputGroup, InputGroupAddon, InputGroupInput } from '@/components/ui/input-group';
import {
	Item,
	ItemActions,
	ItemContent,
	ItemDescription,
	ItemMedia,
	ItemTitle,
} from '@/components/ui/item';
import { useSkills } from '@/hooks/useSkills.ts';
import { useTranslation } from '@/i18n/useI18n.ts';

interface SkillPanelProps {
	/** The skills equipped in the workspace. */
	skills: Skill[];
	/** Whether the skill list is still loading. */
	loading?: boolean;
	/**
	 * Upload a picked folder as a skill.
	 *
	 * @param files - The folder's files, carrying `webkitRelativePath`.
	 * @param options - Progress and abort hooks.
	 */
	onUpload: (files: File[], options?: UploadOptions) => Promise<void>;
	/**
	 * Install skills the user already has.
	 *
	 * @param skillIds - The library record ids to add.
	 */
	onAddFromLibrary: (skillIds: string[]) => Promise<void>;
	/**
	 * Remove a skill by name.
	 *
	 * @param name - The skill name to remove.
	 */
	onRemove: (name: string) => Promise<void>;
}

/**
 * Pure content body for the Skill dock panel: a search box, the list
 * of equipped skills, and an "Add Skill" action. Holds only local UI
 * state (search text, delete confirmation target); all data arrives
 * via props so it owns no data fetching.
 *
 * Renders without its own header/border — the surrounding `Panel`
 * chrome (from `PanelDock`) provides those.
 *
 * @param skills - The skills to list.
 * @param loading - Whether the list is loading.
 * @param onUpload - Folder-upload callback.
 * @param onAddFromLibrary - Library-install callback.
 * @param onRemove - Remove-skill callback.
 * @returns The skill panel body.
 */
export function SkillPanel({
	skills,
	loading = false,
	onUpload,
	onAddFromLibrary,
	onRemove,
}: SkillPanelProps) {
	const { t } = useTranslation();
	const [search, setSearch] = useState('');
	const [deleteOpen, setDeleteOpen] = useState(false);
	const [deleteTarget, setDeleteTarget] = useState<string | null>(null);
	// The workspace stores only the skill itself, so the icon and author
	// are looked up in the library, matched on the shared name.
	const { skills: library } = useSkills();
	const byName = new Map(library.map((skill) => [skill.name, skill]));

	const filtered = search
		? skills.filter((s) => s.name.toLowerCase().includes(search.toLowerCase()))
		: skills;

	return (
		<div className="flex flex-col flex-1 min-h-0 gap-y-2">
			<span className="text-muted-foreground text-sm">{t('panel.skill.description')}</span>
			<InputGroup>
				<InputGroupInput
					placeholder={t('panel.skill.searchPlaceholder')}
					value={search}
					onChange={(e) => setSearch(e.target.value)}
				/>
				<InputGroupAddon align="inline-end">
					<Search />
				</InputGroupAddon>
			</InputGroup>

			{loading ? (
				<div className="flex flex-1 items-center justify-center">
					<p className="text-muted-foreground text-sm">{t('panel.loading')}</p>
				</div>
			) : filtered.length === 0 ? (
				<PanelEmpty
					icon={search ? SearchX : FileX}
					title={search ? t('panel.search.emptyTitle') : t('panel.skill.emptyTitle')}
					description={
						search
							? t('panel.search.emptyDescription', { query: search })
							: t('panel.skill.emptyDescription')
					}
				/>
			) : (
				<div className="flex flex-col flex-1 min-h-0 overflow-y-auto scroll-fade gap-y-2">
					{filtered.map((skill) => {
						const installed = byName.get(skill.name);
						return (
							<Item key={skill.name} variant="outline" className="group/skill">
								<ItemMedia>
									<Avatar className="rounded-md">
										<AvatarImage
											src={installed?.icon_url ?? undefined}
											alt={skill.name}
											loading="lazy"
										/>
										<AvatarFallback className="rounded-md">
											{skill.name.slice(0, 1).toUpperCase()}
										</AvatarFallback>
									</Avatar>
								</ItemMedia>
								<ItemContent>
									<ItemTitle>
										<span className="truncate font-medium">{skill.name}</span>
										{installed?.author && (
											<span className="text-xs text-muted-foreground">
												@{installed.author}
											</span>
										)}
									</ItemTitle>
									<ItemDescription className="line-clamp-2">
										{skill.description}
									</ItemDescription>
								</ItemContent>
								<ItemActions>
									{/* Only on hover: deleting is rare, and a
									    button on every row competes with the
									    content for attention. */}
									<Button
										variant="secondary"
										size="icon-sm"
										className="opacity-0 transition-opacity group-hover/skill:opacity-100 focus-visible:opacity-100"
										onClick={() => {
											setDeleteTarget(skill.name);
											setDeleteOpen(true);
										}}
										title={t('common.delete')}
									>
										<Trash className="size-3" />
									</Button>
								</ItemActions>
							</Item>
						);
					})}
				</div>
			)}

			<AddSkillDialog
				present={new Set(skills.map((s) => s.name))}
				onUpload={onUpload}
				onAddFromLibrary={onAddFromLibrary}
			>
				<Button variant="default">
					<PlusCircle />
					{t('panel.skill.add')}
				</Button>
			</AddSkillDialog>

			<DeleteDialog
				open={deleteOpen}
				onOpenChange={setDeleteOpen}
				title={t('common.deleteTitle', {
					entity: t('dialog-mcp-delete.skillEntity'),
					name: deleteTarget ?? '',
				})}
				description={t('dialog-mcp-delete.skillDescription')}
				onConfirm={async () => {
					if (deleteTarget) await onRemove(deleteTarget);
				}}
			/>
		</div>
	);
}
