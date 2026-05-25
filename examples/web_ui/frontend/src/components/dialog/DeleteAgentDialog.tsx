import { useTranslation } from 'react-i18next';

import { DeleteDialog } from './DeleteDialog';
import type { AgentRecord } from '@/api';
import { useAgents } from '@/hooks/useAgents';

interface Props {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	agent: AgentRecord;
	onDeleted?: () => void;
}

export function DeleteAgentDialog({ open, onOpenChange, agent, onDeleted }: Props) {
	const { remove } = useAgents();
	const { t } = useTranslation();

	return (
		<DeleteDialog
			open={open}
			onOpenChange={onOpenChange}
			title={t('dialog-agent-delete.title')}
			description={
				<>
					<span className="font-medium text-foreground">{agent.data.name}</span>
					{' — '}
					{t('dialog-agent-delete.description')}
				</>
			}
			confirmLabel={t('dialog-agent-delete.confirm')}
			onConfirm={async () => {
				await remove(agent.id);
				onDeleted?.();
			}}
		/>
	);
}
