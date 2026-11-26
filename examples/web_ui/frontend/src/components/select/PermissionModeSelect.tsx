import { ChevronDown, UserRoundKey } from 'lucide-react';

import type { PermissionConstraints, PermissionMode } from '@/api/types';
import { Button } from '@/components/ui/button';
import {
	DropdownMenu,
	DropdownMenuCheckboxItem,
	DropdownMenuContent,
	DropdownMenuGroup,
	DropdownMenuItem,
	DropdownMenuLabel,
	DropdownMenuSeparator,
	DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { useTranslation } from '@/i18n/useI18n.ts';
import { cn } from '@/lib/utils.ts';

const PERMISSION_MODES: PermissionMode[] = ['ask', 'auto', 'bypass'];

interface Props extends Omit<React.ComponentPropsWithoutRef<typeof Button>, 'onChange' | 'value'> {
	className?: string;
	value?: PermissionMode;
	disabled?: boolean;
	onChange?: (value: PermissionMode) => void;
	constraints?: PermissionConstraints;
	onConstraintsChange?: (updates: PermissionConstraints) => void;
	unattended?: boolean;
}

export function PermissionModeSelect({
	className,
	value,
	disabled,
	onChange,
	constraints,
	onConstraintsChange,
	unattended = false,
	...props
}: Props) {
	const { t } = useTranslation();

	const displayLabel = value ? t(`permission-mode.${value}`) : t('permission-mode.placeholder');

	return (
		<DropdownMenu>
			<DropdownMenuTrigger asChild>
				<Button
					variant="outline"
					size="sm"
					className={cn('justify-between gap-1 font-normal', className)}
					disabled={disabled}
					tooltip={t('permission-mode.trigger-tooltip')}
					{...props}
				>
					<div className="flex flex-row items-center gap-x-2">
						<UserRoundKey />
						<span className="truncate">{displayLabel}</span>
					</div>
					<ChevronDown className="size-3.5 text-muted-foreground" />
				</Button>
			</DropdownMenuTrigger>
			<DropdownMenuContent align="start" className="min-w-48">
				<DropdownMenuGroup>
					<DropdownMenuLabel>{t('permission-mode.label')}</DropdownMenuLabel>
					{PERMISSION_MODES.map((mode) => (
						<Tooltip key={mode}>
							<TooltipTrigger asChild>
								<DropdownMenuItem onSelect={() => onChange?.(mode)}>
									{t(`permission-mode.${mode}`)}
								</DropdownMenuItem>
							</TooltipTrigger>
							<TooltipContent side="right">
								{t(`permission-mode.${mode}-tooltip`)}
							</TooltipContent>
						</Tooltip>
					))}
				</DropdownMenuGroup>
				{onConstraintsChange && (
					<>
						<DropdownMenuSeparator />
						<DropdownMenuLabel>{t('permission-mode.constraints')}</DropdownMenuLabel>
						<DropdownMenuCheckboxItem
							checked={constraints?.read_only ?? false}
							disabled={disabled}
							onCheckedChange={(checked) =>
								onConstraintsChange({ read_only: checked })
							}
						>
							{t('permission-mode.read-only')}
						</DropdownMenuCheckboxItem>
						<DropdownMenuCheckboxItem
							checked={constraints?.allow_workspace_edits ?? false}
							disabled={disabled}
							onCheckedChange={(checked) =>
								onConstraintsChange({ allow_workspace_edits: checked })
							}
						>
							{t('permission-mode.workspace-edits')}
						</DropdownMenuCheckboxItem>
						{!unattended && (
							<DropdownMenuCheckboxItem
								checked={constraints?.interactive ?? true}
								disabled={disabled}
								onCheckedChange={(checked) =>
									onConstraintsChange({ interactive: checked })
								}
							>
								{t('permission-mode.interactive')}
							</DropdownMenuCheckboxItem>
						)}
					</>
				)}
			</DropdownMenuContent>
		</DropdownMenu>
	);
}
