import { jsonrepair } from 'jsonrepair';
import { ChevronRight } from 'lucide-react';

import { getResultText, parseInput, toolArgClass, toolLabelClass } from './_shared';
import type { TFunction, ToolCallWithResult, ToolRenderer } from './types';

function asRecord(value: unknown): Record<string, unknown> | undefined {
	return value !== null && typeof value === 'object' && !Array.isArray(value)
		? (value as Record<string, unknown>)
		: undefined;
}

/** Unwrap only successful text-only tool envelopes with no metadata to hide. */
function textPreview(raw: string): string {
	const trimmed = raw.trim();
	if (!trimmed.startsWith('{') || !trimmed.endsWith('}')) return raw;
	try {
		const result = asRecord(JSON.parse(jsonrepair(trimmed)));
		const metadata = asRecord(result?.metadata);
		if (
			!result ||
			Object.keys(result).length !== 3 ||
			result.state !== 'success' ||
			!metadata ||
			Object.keys(metadata).length > 0 ||
			!Array.isArray(result.content) ||
			result.content.length === 0
		)
			return raw;
		const blocks = result.content.map(asRecord);
		if (!blocks.every((block) => block?.type === 'text' && typeof block.text === 'string')) {
			return raw;
		}
		return blocks.map((block) => block!.text as string).join('\n');
	} catch {
		return raw;
	}
}

function getCode(pair: ToolCallWithResult): string {
	const input = parseInput(pair.call.input);
	return typeof input.code === 'string' ? input.code : pair.call.input;
}

function getOutput(pair: ToolCallWithResult): string {
	const raw = getResultText(pair.result);
	const metadata = asRecord(pair.result?.metadata?.code_mode);
	return metadata?.truncated === true ? raw : textPreview(raw);
}

function renderCodeModeBody(pair: ToolCallWithResult, t: TFunction) {
	const metadata = asRecord(pair.result?.metadata?.code_mode);
	const entries = Array.isArray(metadata?.calls) ? metadata.calls.map(asRecord) : undefined;
	const calls = entries?.every(
		(entry) => typeof entry?.name === 'string' && typeof entry.state === 'string',
	)
		? entries
		: undefined;
	const output = getOutput(pair);
	const states: Record<string, string> = {
		calling: t('common.running'),
		success: t('common.completed'),
		error: t('common.failed'),
		interrupted: t('tool.codeMode.interrupted'),
		denied: t('tool.codeMode.denied'),
	};
	return (
		<div className="mt-2 min-w-0 overflow-hidden rounded-md border bg-background text-xs">
			<div className="space-y-2 p-3">
				<div className="font-medium text-muted-foreground">{t('tool.codeMode.output')}</div>
				{output ? (
					<pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words font-mono">
						{output}
					</pre>
				) : (
					<p className="text-muted-foreground">
						{t(
							!pair.result || pair.result.state === 'running'
								? 'common.running'
								: 'tool.codeMode.noOutput',
						)}
					</p>
				)}
				{typeof metadata?.error === 'string' && metadata.error && (
					<pre className="max-h-40 overflow-auto whitespace-pre-wrap break-words text-destructive">
						{metadata.error}
					</pre>
				)}
				{metadata?.truncated === true && (
					<p className="text-muted-foreground">{t('tool.codeMode.truncated')}</p>
				)}
			</div>
			<details className="group/details border-t">
				<summary className="flex cursor-pointer list-none items-center gap-1.5 px-3 py-2 text-muted-foreground hover:text-foreground [&::-webkit-details-marker]:hidden">
					<ChevronRight className="size-3 shrink-0 transition-transform group-open/details:rotate-90" />
					{t('tool.codeMode.details')}
				</summary>
				<div className="space-y-4 px-3 pb-3">
					<section className="space-y-2">
						<h4 className="font-medium">{t('tool.codeMode.python')}</h4>
						<pre className="max-h-64 overflow-auto rounded bg-muted/40 p-2 font-mono">
							{getCode(pair)}
						</pre>
					</section>
					<section className="space-y-2">
						<h4 className="font-medium">{t('tool.codeMode.calls')}</h4>
						{calls && calls.length > 0 ? (
							<ul className="space-y-1.5">
								{calls.map((entry, index) => (
									<li
										key={index}
										className="flex min-w-0 items-start justify-between gap-3"
									>
										<span className="min-w-0 break-all font-mono">
											{entry!.name as string}
										</span>
										<span className="shrink-0 text-muted-foreground">
											{states[entry!.state as string] ??
												(entry!.state as string)}
										</span>
									</li>
								))}
							</ul>
						) : (
							<p className="text-muted-foreground">
								{t(
									calls
										? 'tool.codeMode.noCalls'
										: 'tool.codeMode.callsUnavailable',
								)}
							</p>
						)}
					</section>
					{pair.result && (
						<section className="space-y-2">
							<h4 className="font-medium">{t('tool.codeMode.raw')}</h4>
							<pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words rounded bg-muted/40 p-2 font-mono">
								{JSON.stringify(pair.result, null, 2)}
							</pre>
						</section>
					)}
				</div>
			</details>
		</div>
	);
}

export const CodeModeRenderer: ToolRenderer = {
	getDisplayName: () => 'Code Mode',
	renderHeader: (pair) => (
		<>
			<span className={toolLabelClass}>Code Mode</span>
			<span className={toolArgClass}>{getOutput(pair).replace(/\s+/g, ' ').trim()}</span>
		</>
	),
	renderBody: renderCodeModeBody,
};
