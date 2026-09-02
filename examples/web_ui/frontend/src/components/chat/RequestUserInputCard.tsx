import type { ToolCallBlock } from '@agentscope-ai/agentscope/message';
import { Check, CornerDownLeft, PencilLine, X } from 'lucide-react';
import { type KeyboardEvent, useEffect, useMemo, useRef, useState } from 'react';

import { Button } from '@/components/ui/button';
import { Spinner } from '@/components/ui/spinner';
import { Textarea } from '@/components/ui/textarea';
import { useTranslation } from '@/i18n/useI18n';
import {
	getInitialRadioIndex,
	getNextRadioIndex,
	getRadioEnterAction,
	parseRequestUserInputPayload,
	shouldSubmitOtherInput,
	type RequestUserInputPayload,
	type RequestUserInputResult,
} from '@/lib/request-user-input';
import { cn } from '@/lib/utils';

type RequestUserInputSelection = number | 'other';

const INVALID_PAYLOAD: RequestUserInputPayload = {
	question: '',
	options: [],
};

export function RequestUserInputCard({
	toolCall,
	onSubmit,
	onCancel,
	cancelling = false,
}: {
	toolCall: ToolCallBlock;
	onSubmit: (result: RequestUserInputResult) => Promise<void>;
	onCancel?: () => void;
	cancelling?: boolean;
}) {
	const { t } = useTranslation();
	const parsedPayload = useMemo(
		() => parseRequestUserInputPayload(toolCall.input),
		[toolCall.input],
	);
	const payload = parsedPayload ?? INVALID_PAYLOAD;
	const initialIndex = useMemo(() => getInitialRadioIndex(payload.options), [payload.options]);
	const [selected, setSelected] = useState<RequestUserInputSelection | null>(initialIndex);
	const [otherText, setOtherText] = useState('');
	const [submitting, setSubmitting] = useState(false);
	const otherInputRef = useRef<HTMLTextAreaElement>(null);
	const optionRefs = useRef<Array<HTMLButtonElement | null>>([]);
	const focusOtherInputRef = useRef(false);

	useEffect(() => {
		if (initialIndex !== null) optionRefs.current[initialIndex]?.focus();
	}, [initialIndex]);

	useEffect(() => {
		if (selected === 'other' && focusOtherInputRef.current) {
			focusOtherInputRef.current = false;
			otherInputRef.current?.focus();
		}
	}, [selected]);

	const selectOption = (selection: RequestUserInputSelection) => {
		focusOtherInputRef.current = selection === 'other';
		if (selection === 'other' && selected === 'other') {
			otherInputRef.current?.focus();
			return;
		}
		setSelected(selection);
	};

	const submitSelection = async (selection: RequestUserInputSelection) => {
		const text = otherText.trim();
		if (submitting || (selection === 'other' && text.length === 0)) return;

		setSubmitting(true);
		try {
			if (selection === 'other') {
				await onSubmit({ type: 'other', text });
			} else {
				await onSubmit({
					type: 'option',
					option_index: selection,
					label: payload.options[selection].label,
				});
			}
		} catch {
			setSubmitting(false);
		}
	};

	const handleOptionKeyDown = (event: KeyboardEvent<HTMLButtonElement>, currentIndex: number) => {
		if (event.key === 'Enter') {
			event.preventDefault();
			const selection = currentIndex === payload.options.length ? 'other' : currentIndex;
			const action = getRadioEnterAction(currentIndex, payload.options.length, otherText);
			if (action === 'edit-other') {
				selectOption('other');
			} else {
				void submitSelection(selection);
			}
			return;
		}

		const itemCount = payload.options.length + 1;
		const nextIndex = getNextRadioIndex(currentIndex, event.key, itemCount);
		if (nextIndex === null) return;

		event.preventDefault();
		focusOtherInputRef.current = false;
		setSelected(nextIndex === payload.options.length ? 'other' : nextIndex);
		optionRefs.current[nextIndex]?.focus();
	};

	const canSubmit = selected !== null && (selected !== 'other' || otherText.trim().length > 0);

	const handleSubmit = () => {
		if (selected !== null) void submitSelection(selected);
	};

	if (!parsedPayload) {
		return (
			<section className="w-full rounded-[28px] bg-muted px-5 py-5 text-sm ring-1 ring-border sm:px-6">
				<h2 className="font-medium text-foreground">
					{t('requestUserInput.invalidTitle')}
				</h2>
				<p className="mt-1 leading-5 text-muted-foreground">
					{t('requestUserInput.invalidDescription')}
				</p>
				{onCancel ? (
					<div className="mt-4 flex justify-end">
						<Button
							variant="outline"
							disabled={cancelling}
							onClick={onCancel}
							className="gap-2"
						>
							{cancelling ? <Spinner className="size-4" /> : <X className="size-4" />}
							{t(
								cancelling
									? 'requestUserInput.cancelling'
									: 'requestUserInput.cancel',
							)}
						</Button>
					</div>
				) : null}
			</section>
		);
	}

	return (
		<section className="max-h-[min(70vh,36rem)] w-full overflow-y-auto rounded-[28px] bg-muted px-5 py-5 text-sm ring-1 ring-border sm:px-6">
			<div className="mb-4 space-y-1.5">
				<p className="text-xs font-medium uppercase tracking-[0.14em] text-muted-foreground">
					{t('requestUserInput.eyebrow')}
				</p>
				<h2 className="text-base font-medium leading-6 text-foreground">
					{payload.question}
				</h2>
			</div>

			<div className="space-y-2" role="radiogroup" aria-label={payload.question}>
				{payload.options.map((option, index) => {
					const active = selected === index;
					return (
						<button
							key={`${index}:${option.label}`}
							ref={(element) => {
								optionRefs.current[index] = element;
							}}
							type="button"
							role="radio"
							aria-checked={active}
							tabIndex={selected === null ? (index === 0 ? 0 : -1) : active ? 0 : -1}
							disabled={submitting}
							onClick={() => selectOption(index)}
							onKeyDown={(event) => handleOptionKeyDown(event, index)}
							className={cn(
								'group flex w-full items-start gap-3 rounded-xl border px-3.5 py-3 text-left transition-colors',
								'focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-ring/50',
								active
									? 'border-foreground/20 bg-background text-foreground shadow-sm'
									: 'border-border/70 bg-background/45 text-secondary-foreground hover:bg-background/80',
							)}
						>
							<span
								className={cn(
									'mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full border',
									active
										? 'border-foreground bg-foreground text-background'
										: 'border-border bg-background',
								)}
							>
								{active ? <Check className="size-3" /> : null}
							</span>
							<span className="min-w-0 flex-1">
								<span className="flex flex-wrap items-center gap-2 font-medium">
									{option.label}
									{option.recommended ? (
										<span className="rounded-full bg-foreground/7 px-2 py-0.5 text-[11px] font-medium text-muted-foreground">
											{t('requestUserInput.recommended')}
										</span>
									) : null}
								</span>
								{option.description ? (
									<span className="mt-0.5 block leading-5 text-muted-foreground">
										{option.description}
									</span>
								) : null}
							</span>
						</button>
					);
				})}

				<button
					ref={(element) => {
						optionRefs.current[payload.options.length] = element;
					}}
					type="button"
					role="radio"
					aria-checked={selected === 'other'}
					tabIndex={selected === 'other' ? 0 : -1}
					disabled={submitting}
					onClick={() => selectOption('other')}
					onKeyDown={(event) => handleOptionKeyDown(event, payload.options.length)}
					className={cn(
						'flex w-full items-center gap-3 rounded-xl border px-3.5 py-3 text-left transition-colors',
						'focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-ring/50',
						selected === 'other'
							? 'border-foreground/20 bg-background text-foreground shadow-sm'
							: 'border-border/70 bg-background/45 text-secondary-foreground hover:bg-background/80',
					)}
				>
					<span
						className={cn(
							'flex size-5 shrink-0 items-center justify-center rounded-full border',
							selected === 'other'
								? 'border-foreground bg-foreground text-background'
								: 'border-border bg-background',
						)}
					>
						<PencilLine className="size-3" />
					</span>
					<span className="font-medium">{t('requestUserInput.other')}</span>
				</button>
			</div>

			{selected === 'other' ? (
				<Textarea
					ref={otherInputRef}
					value={otherText}
					disabled={submitting}
					maxLength={500}
					placeholder={t('requestUserInput.otherPlaceholder')}
					onChange={(event) => setOtherText(event.target.value)}
					onKeyDown={(event) => {
						if (
							shouldSubmitOtherInput(
								event.key,
								event.shiftKey,
								event.nativeEvent.isComposing,
							)
						) {
							event.preventDefault();
							void submitSelection('other');
						}
					}}
					className="mt-3 min-h-20 resize-y bg-background"
				/>
			) : null}

			<div className="mt-4 flex items-center justify-between gap-3">
				{onCancel ? (
					<Button
						variant="ghost"
						disabled={submitting || cancelling}
						onClick={onCancel}
						className="gap-2 text-muted-foreground"
					>
						{cancelling ? <Spinner className="size-4" /> : <X className="size-4" />}
						{t(cancelling ? 'requestUserInput.cancelling' : 'requestUserInput.cancel')}
					</Button>
				) : (
					<span />
				)}
				<Button
					disabled={!canSubmit || submitting}
					onClick={handleSubmit}
					className="gap-2"
				>
					{submitting ? (
						<Spinner className="size-4" />
					) : (
						<CornerDownLeft className="size-4" />
					)}
					{t('requestUserInput.submit')}
				</Button>
			</div>
		</section>
	);
}
