import type { ToolCallBlock } from '@agentscope-ai/agentscope/message';
import { Braces, Check, FileText, Wrench, X } from 'lucide-react';
import { useMemo, useState } from 'react';

import { Button } from '@/components/ui/button';
import { Spinner } from '@/components/ui/spinner';
import { Textarea } from '@/components/ui/textarea';
import { useTranslation } from '@/i18n/useI18n';
import type { ClientExternalToolResult } from '@/lib/client-external-tools';
import { cn } from '@/lib/utils';

type ResultMode = 'text' | 'json';

function formatToolInput(input: string): string {
	try {
		return JSON.stringify(JSON.parse(input), null, 2);
	} catch {
		return input;
	}
}

export function ManualClientToolCard({
	toolCall,
	onSubmit,
	onCancel,
	cancelling,
}: {
	toolCall: ToolCallBlock;
	onSubmit: (result: ClientExternalToolResult) => Promise<void>;
	onCancel?: () => void;
	cancelling: boolean;
}) {
	const { t } = useTranslation();
	const [mode, setMode] = useState<ResultMode>('text');
	const [textResult, setTextResult] = useState('');
	const [jsonResult, setJsonResult] = useState('{}');
	const [jsonError, setJsonError] = useState(false);
	const [submitting, setSubmitting] = useState(false);
	const formattedInput = useMemo(() => formatToolInput(toolCall.input), [toolCall.input]);

	const handleSubmit = async () => {
		if (submitting) return;
		let result: ClientExternalToolResult;
		if (mode === 'text') {
			const text = textResult.trim();
			if (!text) return;
			result = text;
		} else {
			try {
				result = JSON.parse(jsonResult) as ClientExternalToolResult;
				setJsonError(false);
			} catch {
				setJsonError(true);
				return;
			}
		}

		setSubmitting(true);
		try {
			await onSubmit(result);
		} catch {
			setSubmitting(false);
		}
	};

	const canSubmit = mode === 'json' || textResult.trim().length > 0;

	return (
		<section className="max-h-[min(72vh,40rem)] w-full overflow-y-auto rounded-[28px] bg-muted px-5 py-5 text-sm ring-1 ring-border sm:px-6">
			<div className="flex items-start gap-3">
				<div className="flex size-8 shrink-0 items-center justify-center rounded-full bg-background text-muted-foreground ring-1 ring-border">
					<Wrench className="size-4" />
				</div>
				<div className="min-w-0 flex-1">
					<p className="text-xs font-medium uppercase tracking-[0.14em] text-muted-foreground">
						{t('clientTools.manual.eyebrow')}
					</p>
					<h2 className="mt-1 break-all text-base font-medium leading-6 text-foreground">
						{toolCall.name}
					</h2>
					<p className="mt-1 leading-5 text-muted-foreground">
						{t('clientTools.manual.description')}
					</p>
				</div>
			</div>

			<details className="group mt-4 overflow-hidden rounded-xl bg-background/60 ring-1 ring-border/70">
				<summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2.5 font-medium text-muted-foreground outline-none focus-visible:ring-3 focus-visible:ring-ring/50 [&::-webkit-details-marker]:hidden">
					<Braces className="size-3.5" />
					{t('clientTools.manual.arguments')}
				</summary>
				<pre className="max-h-48 overflow-auto border-t border-border/70 p-3 font-mono text-xs leading-5 text-secondary-foreground">
					{formattedInput}
				</pre>
			</details>

			<div className="mt-4">
				<div className="inline-flex rounded-lg bg-background/60 p-1 ring-1 ring-border/70">
					<Button
						type="button"
						variant="ghost"
						size="sm"
						className={cn(mode === 'text' && 'bg-background shadow-sm')}
						onClick={() => setMode('text')}
					>
						<FileText className="size-3.5" />
						{t('clientTools.manual.text')}
					</Button>
					<Button
						type="button"
						variant="ghost"
						size="sm"
						className={cn(mode === 'json' && 'bg-background shadow-sm')}
						onClick={() => setMode('json')}
					>
						<Braces className="size-3.5" />
						JSON
					</Button>
				</div>

				<Textarea
					value={mode === 'text' ? textResult : jsonResult}
					disabled={submitting || cancelling}
					spellCheck={mode === 'text'}
					aria-invalid={mode === 'json' && jsonError}
					className={cn(
						'mt-2 min-h-28 resize-y bg-background',
						mode === 'json' && 'font-mono text-xs leading-5',
					)}
					placeholder={t(
						mode === 'text'
							? 'clientTools.manual.textPlaceholder'
							: 'clientTools.manual.jsonPlaceholder',
					)}
					onChange={(event) => {
						if (mode === 'text') setTextResult(event.target.value);
						else {
							setJsonResult(event.target.value);
							setJsonError(false);
						}
					}}
				/>
				{mode === 'json' && jsonError ? (
					<p className="mt-1.5 text-xs text-destructive" role="alert">
						{t('clientTools.manual.invalidJson')}
					</p>
				) : null}
			</div>

			<div className="mt-4 flex justify-end gap-2">
				{onCancel ? (
					<Button
						type="button"
						variant="outline"
						disabled={submitting || cancelling}
						onClick={onCancel}
					>
						{cancelling ? <Spinner className="size-4" /> : <X className="size-4" />}
						{t(cancelling ? 'clientTools.cancelling' : 'clientTools.cancel')}
					</Button>
				) : null}
				<Button
					type="button"
					disabled={!canSubmit || submitting || cancelling}
					onClick={() => void handleSubmit()}
				>
					{submitting ? <Spinner className="size-4" /> : <Check className="size-4" />}
					{t('clientTools.manual.submit')}
				</Button>
			</div>
		</section>
	);
}
