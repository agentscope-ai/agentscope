import { EventType, type ExternalExecutionResultEvent } from '@agentscope-ai/agentscope/event';
import type { ToolCallBlock } from '@agentscope-ai/agentscope/message';
import { Check, CircleHelp, PlugZap, Send } from 'lucide-react';
import { useMemo, useRef, useState } from 'react';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Spinner } from '@/components/ui/spinner';
import { useTranslation } from '@/i18n/useI18n';
import { cn } from '@/lib/utils';

interface AskUserOption {
	label: string;
	description: string;
	preview?: string | null;
}

interface AskUserQuestion {
	question: string;
	header: string;
	context?: string | null;
	options: AskUserOption[];
	multi_select?: boolean;
}

interface AskUserParams {
	questions: AskUserQuestion[];
}

interface AnswerDraft {
	selected: string[];
	other: string;
	usesOther: boolean;
}

function parseAskUserParams(input: string): AskUserParams | null {
	try {
		const value = JSON.parse(input) as Partial<AskUserParams>;
		if (!Array.isArray(value.questions) || value.questions.length === 0) return null;
		if (
			!value.questions.every(
				(question) =>
					typeof question?.question === 'string' &&
					typeof question.header === 'string' &&
					Array.isArray(question.options) &&
					question.options.length > 0 &&
					question.options.every(
						(option) =>
							typeof option?.label === 'string' &&
							typeof option.description === 'string',
					),
			)
		) {
			return null;
		}
		return value as AskUserParams;
	} catch {
		return null;
	}
}

function AskUserCard({
	replyId,
	toolCall,
	onResult,
}: {
	replyId: string;
	toolCall: ToolCallBlock;
	onResult: (event: ExternalExecutionResultEvent) => Promise<void>;
}) {
	const { t } = useTranslation();
	const params = useMemo(() => parseAskUserParams(toolCall.input), [toolCall.input]);
	const [answers, setAnswers] = useState<AnswerDraft[]>(() =>
		(params?.questions ?? []).map(() => ({ selected: [], other: '', usesOther: false })),
	);
	const [submitting, setSubmitting] = useState(false);
	const submittingRef = useRef(false);

	if (!params) {
		return (
			<div className="rounded-[28px] bg-muted p-5 ring ring-border">
				<div className="flex items-center gap-2 text-sm font-medium">
					<CircleHelp className="size-4" />
					{t('chat.askUser.invalid')}
				</div>
			</div>
		);
	}

	const updateAnswer = (index: number, update: (answer: AnswerDraft) => AnswerDraft) => {
		setAnswers((current) =>
			current.map((answer, answerIndex) => (answerIndex === index ? update(answer) : answer)),
		);
	};

	const complete = answers.every(
		(answer) => answer.selected.length > 0 || answer.other.trim().length > 0,
	);

	const submit = async () => {
		if (!complete || submittingRef.current) return;
		submittingRef.current = true;
		setSubmitting(true);
		const submittedAnswers = params.questions.map((question, index) => ({
			question: question.question,
			selected: answers[index].usesOther ? [] : answers[index].selected,
			other: answers[index].usesOther ? answers[index].other.trim() : null,
		}));
		const output = submittedAnswers
			.map((answer) => `${answer.question}\n${answer.other ?? answer.selected.join(', ')}`)
			.join('\n\n');
		const createdAt = new Date().toISOString();
		try {
			await onResult({
				type: EventType.EXTERNAL_EXECUTION_RESULT,
				id: crypto.randomUUID(),
				created_at: createdAt,
				reply_id: replyId,
				execution_results: [
					{
						type: 'tool_result',
						id: toolCall.id,
						name: toolCall.name,
						output,
						state: 'success',
						metadata: { answers: submittedAnswers },
						created_at: createdAt,
						finished_at: createdAt,
					},
				],
			});
		} catch {
			submittingRef.current = false;
			setSubmitting(false);
		}
	};

	return (
		<div className="max-h-[min(70vh,560px)] w-full space-y-5 overflow-y-auto rounded-[28px] bg-muted px-6 py-5 text-sm ring ring-border">
			<div className="flex items-center gap-2 text-secondary-foreground">
				<CircleHelp className="size-4 shrink-0" />
				<strong>{t('chat.askUser.title')}</strong>
			</div>
			{params.questions.map((question, questionIndex) => {
				const answer = answers[questionIndex];
				const preview = question.options.find(
					(option) => answer.selected.includes(option.label) && option.preview,
				)?.preview;
				return (
					<section key={question.question} className="space-y-3">
						<div className="space-y-1">
							<span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
								{question.header}
							</span>
							<p className="font-medium text-foreground">{question.question}</p>
							{question.context ? (
								<p className="whitespace-pre-wrap text-muted-foreground">
									{question.context}
								</p>
							) : null}
						</div>
						<div className="grid gap-2 sm:grid-cols-2">
							{question.options.map((option) => {
								const selected = answer.selected.includes(option.label);
								return (
									<Button
										key={option.label}
										type="button"
										variant={selected ? 'secondary' : 'outline'}
										className="h-auto min-h-14 justify-start gap-3 whitespace-normal px-3 py-2 text-left"
										aria-pressed={selected}
										disabled={submitting}
										onClick={() =>
											updateAnswer(questionIndex, (current) => ({
												selected: question.multi_select
													? selected
														? current.selected.filter(
																(label) => label !== option.label,
															)
														: [...current.selected, option.label]
													: [option.label],
												other: '',
												usesOther: false,
											}))
										}
									>
										<span
											className={cn(
												'flex size-5 shrink-0 items-center justify-center rounded-full border',
												selected &&
													'border-primary bg-primary text-primary-foreground',
											)}
										>
											{selected ? <Check className="size-3" /> : null}
										</span>
										<span className="min-w-0">
											<span className="block font-medium">
												{option.label}
											</span>
											<span className="block text-xs text-muted-foreground">
												{option.description}
											</span>
										</span>
									</Button>
								);
							})}
						</div>
						<div className="space-y-2">
							<Button
								type="button"
								variant={answer.usesOther ? 'secondary' : 'ghost'}
								size="sm"
								disabled={submitting}
								onClick={() =>
									updateAnswer(questionIndex, (current) => ({
										selected: [],
										other: current.usesOther ? '' : current.other,
										usesOther: !current.usesOther,
									}))
								}
							>
								{t('chat.askUser.other')}
							</Button>
							{answer.usesOther ? (
								<Input
									autoFocus
									value={answer.other}
									placeholder={t('chat.askUser.otherPlaceholder')}
									disabled={submitting}
									onChange={(event) =>
										updateAnswer(questionIndex, (current) => ({
											...current,
											other: event.target.value,
										}))
									}
								/>
							) : null}
						</div>
						{preview ? (
							<pre className="max-h-44 overflow-auto whitespace-pre-wrap rounded-lg border bg-background p-3 font-mono text-xs">
								{preview}
							</pre>
						) : null}
					</section>
				);
			})}
			<div className="flex justify-end border-t pt-4">
				<Button
					type="button"
					disabled={!complete || submitting}
					onClick={() => void submit()}
				>
					{submitting ? <Spinner className="size-4" /> : <Send className="size-4" />}
					{t('chat.askUser.submit')}
				</Button>
			</div>
		</div>
	);
}

export function ExternalExecutionCard({
	replyId,
	toolCall,
	onResult,
}: {
	replyId: string;
	toolCall: ToolCallBlock;
	onResult: (event: ExternalExecutionResultEvent) => Promise<void>;
}) {
	const { t } = useTranslation();
	if (toolCall.name === 'AskUser') {
		return <AskUserCard replyId={replyId} toolCall={toolCall} onResult={onResult} />;
	}
	return (
		<div className="rounded-[28px] bg-muted px-6 py-5 text-sm ring ring-border">
			<div className="flex items-center gap-2 font-medium text-secondary-foreground">
				<PlugZap className="size-4 shrink-0" />
				{t('chat.externalExecution.title', { name: toolCall.name })}
			</div>
			<p className="mt-2 text-muted-foreground">{t('chat.externalExecution.description')}</p>
		</div>
	);
}
