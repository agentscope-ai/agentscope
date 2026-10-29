import type { ToolCallBlock } from '@agentscope-ai/agentscope/message';
import { CircleAlert, X } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Spinner } from '@/components/ui/spinner';
import { useTranslation } from '@/i18n/useI18n';

export function UnsupportedClientToolCard({
	toolCall,
	onCancel,
	cancelling,
}: {
	toolCall: ToolCallBlock;
	onCancel?: () => void;
	cancelling: boolean;
}) {
	const { t } = useTranslation();

	return (
		<section className="w-full rounded-[28px] bg-muted px-5 py-5 text-sm ring-1 ring-border sm:px-6">
			<div className="flex items-start gap-3">
				<div className="flex size-8 shrink-0 items-center justify-center rounded-full bg-background text-muted-foreground ring-1 ring-border">
					<CircleAlert className="size-4" />
				</div>
				<div className="min-w-0 flex-1">
					<h2 className="font-medium text-foreground">
						{t('clientTools.unsupportedTitle')}
					</h2>
					<p className="mt-1 leading-5 text-muted-foreground">
						{t('clientTools.unsupportedDescription', {
							name: toolCall.name,
						})}
					</p>
				</div>
			</div>
			{onCancel ? (
				<div className="mt-4 flex justify-end">
					<Button
						variant="outline"
						disabled={cancelling}
						onClick={onCancel}
						className="gap-2"
					>
						{cancelling ? <Spinner className="size-4" /> : <X className="size-4" />}
						{t(cancelling ? 'clientTools.cancelling' : 'clientTools.cancel')}
					</Button>
				</div>
			) : null}
		</section>
	);
}
