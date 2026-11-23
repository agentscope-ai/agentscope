import { Check, ChevronLeft, ChevronRight, X } from 'lucide-react';
import { useEffect, useRef, useState, type KeyboardEvent } from 'react';

import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { useTranslation } from '@/i18n/useI18n';
import {
	parseAskUserPayload,
	getNextRadioIndex,
	shouldSubmitOtherInput,
	type AskUserAnswer,
	type AskUserQuestion,
} from '@/lib/ask-user';
import type { PendingClientExternalToolProps } from '@/lib/client-external-tools';
import { cn } from '@/lib/utils';

export function AskUserCard(props: PendingClientExternalToolProps) {
	const { t } = useTranslation();
	const payload = parseAskUserPayload(props.toolCall.input);
	if (!payload)
		return (
			<section className="rounded-[28px] bg-muted p-5 text-sm ring-1 ring-border">
				<p role="alert">{t('askUser.invalidDescription')}</p>
				<Button
					className="mt-3"
					variant="outline"
					onClick={props.onCancel}
					disabled={props.cancelling}
				>
					<X />
					{t('askUser.cancel')}
				</Button>
			</section>
		);
	return <AskUserForm key={props.toolCall.input} {...props} questions={payload.questions} />;
}

function AskUserForm({
	questions,
	onSubmit,
	onCancel,
	cancelling,
}: PendingClientExternalToolProps & { questions: AskUserQuestion[] }) {
	const { t } = useTranslation();
	const [index, setIndex] = useState(0);
	const [answers, setAnswers] = useState<AskUserAnswer[]>(() =>
		questions.map((question) => ({
			question: question.question,
			selected: question.multi_select ? [] : [question.options[0].label],
			other: null,
		})),
	);
	const [focused, setFocused] = useState(0);
	const [submitting, setSubmitting] = useState(false);
	const submittingRef = useRef(false);
	const optionsRef = useRef<Array<HTMLButtonElement | null>>([]);
	const otherRef = useRef<HTMLTextAreaElement>(null);
	const question = questions[index];
	const answer = answers[index];
	const disabled = submitting || cancelling;
	const canSubmit = answer.selected.length > 0 || Boolean(answer.other?.trim());

	useEffect(() => {
		const active = optionsRef.current.find(
			(option) => option?.getAttribute('aria-checked') === 'true',
		);
		(active ?? optionsRef.current[0])?.focus();
	}, [index]);

	const update = (next: AskUserAnswer) =>
		setAnswers((current) => current.map((value, i) => (i === index ? next : value)));
	const choose = (optionIndex: number): AskUserAnswer => {
		if (optionIndex === question.options.length) {
			const next = {
				...answer,
				selected: question.multi_select ? answer.selected : [],
				other: question.multi_select && answer.other !== null ? null : (answer.other ?? ''),
			};
			update(next);
			return next;
		}
		const label = question.options[optionIndex].label;
		const selected = question.multi_select
			? question.options
					.map((option) => option.label)
					.filter((value) =>
						value === label
							? !answer.selected.includes(value)
							: answer.selected.includes(value),
					)
			: [label];
		const next = { ...answer, selected, other: question.multi_select ? answer.other : null };
		update(next);
		return next;
	};
	const advance = async (next = answer) => {
		if (disabled || submittingRef.current || (!next.selected.length && !next.other?.trim()))
			return;
		const complete = answers.map((value, i) =>
			i === index ? { ...next, other: next.other?.trim() || null } : value,
		);
		setAnswers(complete);
		if (index < questions.length - 1) {
			setIndex(index + 1);
			return;
		}
		submittingRef.current = true;
		setSubmitting(true);
		try {
			await onSubmit({ answers: complete });
		} catch {
			submittingRef.current = false;
			setSubmitting(false);
		}
	};
	const keyDown = (event: KeyboardEvent<HTMLButtonElement>, optionIndex: number) => {
		if (event.nativeEvent.isComposing || disabled) return;
		if (event.key === 'Enter') {
			event.preventDefault();
			if (optionIndex === question.options.length) {
				if (answer.other?.trim()) void advance();
				else {
					if (answer.other === null) choose(optionIndex);
					otherRef.current?.focus();
				}
			} else void advance(question.multi_select ? answer : choose(optionIndex));
			return;
		}
		const next = getNextRadioIndex(optionIndex, event.key, question.options.length + 1);
		if (next === null) return;
		event.preventDefault();
		setFocused(next);
		if (!question.multi_select) choose(next);
		optionsRef.current[next]?.focus();
	};

	return (
		<section className="flex max-h-[min(72vh,42rem)] flex-col rounded-[28px] bg-muted p-5 text-sm ring-1 ring-border sm:p-6">
			<div className="min-h-0 overflow-y-auto px-1 pb-1">
				<div className="mb-4 flex items-center justify-between gap-3 text-xs text-muted-foreground">
					<span>{question.header}</span>
					<span>
						{index + 1} / {questions.length}
					</span>
				</div>
				<h2 className="text-base font-medium">{question.question}</h2>
				{question.context && (
					<pre className="mt-3 max-h-48 overflow-auto whitespace-pre-wrap break-words rounded-xl bg-background p-3 font-sans text-sm">
						{question.context}
					</pre>
				)}
				{question.multi_select && (
					<p className="mt-2 text-xs text-muted-foreground">{t('askUser.multiSelect')}</p>
				)}
				<div
					className="mt-4 space-y-2"
					role={question.multi_select ? 'group' : 'radiogroup'}
					aria-label={question.question}
				>
					{[...question.options, { label: t('askUser.other'), description: '' }].map(
						(option, optionIndex) => {
							const isOther = optionIndex === question.options.length;
							const active = isOther
								? answer.other !== null
								: answer.selected.includes(option.label);
							return (
								<button
									key={optionIndex}
									ref={(element) => {
										optionsRef.current[optionIndex] = element;
									}}
									type="button"
									role={question.multi_select ? 'checkbox' : 'radio'}
									aria-checked={active}
									tabIndex={focused === optionIndex ? 0 : -1}
									disabled={disabled}
									onFocus={() => setFocused(optionIndex)}
									onKeyDown={(event) => keyDown(event, optionIndex)}
									onClick={() => {
										choose(optionIndex);
										if (isOther) otherRef.current?.focus();
									}}
									className={cn(
										'flex w-full items-start gap-3 rounded-xl border p-3 text-left outline-none focus-visible:ring-3 focus-visible:ring-ring/50',
										active
											? 'border-foreground/20 bg-background'
											: 'border-border/70 bg-background/45 hover:bg-background/80',
									)}
								>
									<span
										className={cn(
											'mt-0.5 flex size-5 shrink-0 items-center justify-center border',
											question.multi_select ? 'rounded-md' : 'rounded-full',
											active && 'bg-foreground text-background',
										)}
									>
										{active && <Check className="size-3" />}
									</span>
									<span className="min-w-0">
										<span className="font-medium">{option.label}</span>
										{option.description && (
											<span className="mt-1 block text-muted-foreground">
												{option.description}
											</span>
										)}
									</span>
								</button>
							);
						},
					)}
				</div>
				{!question.multi_select && question.options[focused]?.preview && (
					<pre className="mt-3 max-h-48 overflow-auto whitespace-pre-wrap break-words rounded-xl bg-background p-3 text-xs">
						{question.options[focused].preview}
					</pre>
				)}
				<Textarea
					ref={otherRef}
					value={answer.other ?? ''}
					disabled={disabled}
					aria-label={t('askUser.other')}
					placeholder={t('askUser.otherPlaceholder')}
					className="mt-3 min-h-20 bg-background"
					onChange={(event) =>
						update({
							...answer,
							selected: question.multi_select ? answer.selected : [],
							other: event.target.value,
						})
					}
					onKeyDown={(event) => {
						if (
							shouldSubmitOtherInput(
								event.key,
								event.shiftKey,
								event.nativeEvent.isComposing,
							)
						) {
							event.preventDefault();
							void advance();
						}
					}}
				/>
			</div>
			<div className="mt-4 flex shrink-0 flex-wrap justify-end gap-2">
				<Button variant="outline" onClick={onCancel} disabled={disabled}>
					<X />
					{t('askUser.cancel')}
				</Button>
				{index > 0 && (
					<Button
						variant="outline"
						disabled={disabled}
						onClick={() => setIndex(index - 1)}
					>
						<ChevronLeft />
						{t('askUser.back')}
					</Button>
				)}
				<Button disabled={disabled || !canSubmit} onClick={() => void advance()}>
					{index < questions.length - 1 ? t('askUser.next') : t('askUser.submit')}
					<ChevronRight />
				</Button>
			</div>
		</section>
	);
}
