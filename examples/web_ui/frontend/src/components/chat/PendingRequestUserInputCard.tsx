import { RequestUserInputCard } from '@/components/chat/RequestUserInputCard';
import type { PendingClientExternalToolProps } from '@/lib/client-external-tools';

export function PendingRequestUserInputCard({
	toolCall,
	onSubmit,
	onCancel,
	cancelling,
}: PendingClientExternalToolProps) {
	return (
		<RequestUserInputCard
			toolCall={toolCall}
			onSubmit={(result) => onSubmit(result)}
			onCancel={onCancel}
			cancelling={cancelling}
		/>
	);
}
