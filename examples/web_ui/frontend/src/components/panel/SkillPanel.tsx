import { FileX, PlusCircle, Search, SearchX, Trash } from 'lucide-react';
import { useState } from 'react';

import type { Skill } from '@/api';
import { AddSkillDialog } from '@/components/dialog/AddSkillDialog.tsx';
import { DeleteDialog } from '@/components/dialog/DeleteDialog.tsx';
import { PanelEmpty } from '@/components/panel/PanelEmpty';
import { Button } from '@/components/ui/button';
import { InputGroup, InputGroupAddon, InputGroupInput } from '@/components/ui/input-group';
import { Item, ItemActions, ItemContent, ItemDescription, ItemTitle } from '@/components/ui/item';
import { useTranslation } from '@/i18n/useI18n.ts';

interface SkillPanelProps {
	/** The skills equipped in the workspace. */
	skills: Skill[];
	/** Whether the skill list is still loading. */
	loading?: boolean;
	/**
	 * Add a skill to the workspace.
	 *
	 * @param skillPath - Path of the skill to add.
	 */
	onAdd: (skillPath: string) => Promise<void>;
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
 * @param onAdd - Add-skill callback.
 * @param onRemove - Remove-skill callback.
 * @returns The skill panel body.
 */
export function SkillPanel({ skills, loading = false, onAdd, onRemove }: SkillPanelProps) {
	const { t } = useTranslation();
	const [search, setSearch] = useState('');
	const [deleteOpen, setDeleteOpen] = useState(false);
	const [deleteTarget, setDeleteTarget] = useState<string | null>(null);

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
				<div className="flex flex-col flex-1 min-h-0 overflow-y-auto gap-y-2">
					{filtered.map((skill) => (
						<Item key={skill.name} variant="outline">
							<ItemContent>
								<ItemTitle>{skill.name}</ItemTitle>
								<ItemDescription>{skill.description}</ItemDescription>
							</ItemContent>
							<ItemActions>
								<Button
									variant="outline"
									size="icon-sm"
									onClick={() => {
										setDeleteTarget(skill.name);
										setDeleteOpen(true);
									}}
								>
									<Trash />
								</Button>
							</ItemActions>
						</Item>
					))}
				</div>
			)}

			<AddSkillDialog onAdd={onAdd}>
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
