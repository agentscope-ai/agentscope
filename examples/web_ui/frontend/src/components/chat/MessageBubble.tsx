import type { ContentBlock, Msg, ToolCallBlock } from '@agentscope-ai/agentscope/message';
import { ArrowDown, ArrowUp, CheckCircle, Copy, Loader2 } from 'lucide-react';
import { useEffect, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

import { ConfirmCard } from './ConfirmCard';
import { renderToolGroup } from './tool-renderers';
import type { TFunction, ToolCallWithResult } from './tool-renderers/types';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { useTranslation } from '@/i18n/useI18n';
import { formatNumber, formatTime } from '@/utils/common';

interface ToolCallGroupBlock {
	type: 'tool_call_group';
	id: string;
	toolName: string;
	calls: ToolCallWithResult[];
}

type ExtendedContentBlock = ContentBlock | ToolCallGroupBlock;

/**
 * Group consecutive tool_call blocks of the same name into a single
 * `tool_call_group`. tool_result blocks are matched by id back onto the
 * call inside the current group. Any non-tool block (or a tool_call with
 * a different name) flushes the current group.
 */
function groupToolCalls(content: ContentBlock[]): ExtendedContentBlock[] {
	const result: ExtendedContentBlock[] = [];
	let currentGroup: ToolCallWithResult[] = [];
	let currentToolName: string | null = null;

	const flush = () => {
		if (currentGroup.length > 0 && currentToolName) {
			result.push({
				type: 'tool_call_group',
				id: crypto.randomUUID(),
				toolName: currentToolName,
				calls: currentGroup,
			});
			currentGroup = [];
			currentToolName = null;
		}
	};

	for (const block of content) {
		if (block.type === 'tool_call') {
			if (currentToolName !== null && currentToolName !== block.name) {
				flush();
			}
			currentToolName = block.name;
			currentGroup.push({ call: block });
		} else if (block.type === 'tool_result') {
			const matchingCall = currentGroup.find((item) => item.call.id === block.id);
			if (matchingCall) {
				matchingCall.result = block;
			} else {
				// No matching call in the current group — emit a synthetic group
				// so the result still renders. Should not happen in practice.
				flush();
				currentToolName = block.name;
				currentGroup.push({
					call: {
						type: 'tool_call',
						id: block.id,
						name: block.name,
						input: '',
						state: 'finished',
					},
					result: block,
				});
				flush();
			}
		} else {
			flush();
			result.push(block);
		}
	}

	flush();
	return result;
}

/**
 * Render a single content block. Tool call groups are dispatched to
 * `renderToolGroup`; the per-group truncation at the first `asking` call
 * (and the trailing ConfirmCard) lives here so renderers only see a clean
 * list of calls.
 */
function renderBlock(
	block: ExtendedContentBlock,
	index: number,
	t: TFunction,
	onUserConfirm?: (
		toolCallBlock: ToolCallBlock,
		confirm: boolean,
		rules?: ToolCallBlock['suggested_rules'],
	) => void,
) {
	switch (block.type) {
		case 'tool_call_group': {
			const firstAsk = block.calls.findIndex((item) => item.call.state === 'asking');
			const visible = firstAsk === -1 ? block.calls : block.calls.slice(0, firstAsk + 1);
			const askingCall = firstAsk === -1 ? null : block.calls[firstAsk].call;
			return (
				<div key={index} className="flex flex-col gap-y-4 text-muted-foreground">
					{renderToolGroup(block.toolName, visible, t)}
					{askingCall && (
						<ConfirmCard
							toolCall={askingCall}
							onUserConfirm={(confirm, rules) => {
								if (onUserConfirm) onUserConfirm(askingCall, confirm, rules);
							}}
						/>
					)}
				</div>
			);
		}
		case 'text':
			return (
				<div key={index} className="prose text-sm w-full min-w-full">
					<ReactMarkdown
						remarkPlugins={[remarkGfm]}
						components={{
							code: ({ className, children, ...props }) => {
								const isInline = !String(className ?? '').startsWith('language-');
								if (isInline) {
									return (
										<code className={`${className ?? ''} break-all`} {...props}>
											{children}
										</code>
									);
								}
								return (
									<div className="relative w-full">
										<Button
											size="icon-xs"
											variant="ghost"
											className="absolute top-0 right-0 z-10"
											onClick={async (e) => {
												e.preventDefault();
												e.stopPropagation();
												await navigator.clipboard.writeText(
													String(children),
												);
											}}
										>
											<Copy />
										</Button>
										<div className="overflow-x-auto max-w-full w-full">
											<code className={className} {...props}>
												{children}
											</code>
										</div>
									</div>
								);
							},
						}}
					>
						{block.text}
					</ReactMarkdown>
				</div>
			);

		case 'thinking':
			return (
				<details key={index} className="text-xs text-muted-foreground">
					<summary className="cursor-pointer select-none">
						{t('messageBubble.thinking')}
					</summary>
					<p className="mt-1 whitespace-pre-wrap">{block.thinking}</p>
				</details>
			);

		case 'data': {
			const dataType = block.source.media_type.split('/')[0];
			let data: string;
			if (block.source.type === 'url') {
				data = block.source.url;
			} else {
				data = `data:${block.source.media_type};base64,${block.source.data}`;
			}
			switch (dataType) {
				case 'image':
					return <img key={index} src={data} alt="Uploaded image" />;
				case 'audio':
					return <audio key={index} controls src={data} />;
				case 'video':
					return <video key={index} controls src={data} />;
			}
			return null;
		}
		default:
			return null;
	}
}

interface MessageBubbleProps {
	message: Msg;
	onUserConfirm: (
		toolCallBlock: ToolCallBlock,
		confirm: boolean,
		replyId: string,
		rules?: ToolCallBlock['suggested_rules'],
	) => void;
}

/**
 * A message bubble component that displays a chat message.
 *
 * Running state is derived from `message.finished_at`: a missing or null
 * `finished_at` means the agent is still producing this reply. The bottom
 * status row shows a single left-aligned badge laid out as
 * `[state-icon] [duration] [↑in ↓out]`:
 *   - State icon: spinning `Loader2` while running, static `CheckCircle`
 *     once finished.
 *   - Duration is `now - created_at` while running (ticking each second),
 *     `finished_at - created_at` once complete.
 *   - Token counts only appear once `usage` is populated with non-zero
 *     values — typically after the message finishes.
 *
 * When `content` is empty and the message is still running, the bubble
 * body is omitted entirely so only the bottom status row renders.
 */
export function MessageBubble({ message, onUserConfirm }: MessageBubbleProps) {
	const isUser = message.role === 'user';
	const { t } = useTranslation();

	const isRunning = !message.finished_at;
	const hasUsage =
		!!message.usage &&
		((message.usage.input_tokens ?? 0) > 0 || (message.usage.output_tokens ?? 0) > 0);

	// Tick once per second while running so the elapsed time updates live.
	const [now, setNow] = useState(() => Date.now());
	useEffect(() => {
		if (!isRunning) return;
		const id = setInterval(() => setNow(Date.now()), 1000);
		return () => clearInterval(id);
	}, [isRunning]);

	const blocks = groupToolCalls(message.content);
	const showBody = blocks.length > 0;
	const showFooter = !isUser;

	const startMs = new Date(message.created_at).getTime();
	const endMs = isRunning ? now : new Date(message.finished_at!).getTime();
	const elapsedSeconds = Math.max(0, (endMs - startMs) / 1000);
	const elapsedText = formatTime(elapsedSeconds);

	return (
		<div
			className={`flex flex-col w-full max-w-full ${isUser ? 'items-end' : 'items-start'} mb-4`}
		>
			{showBody && (
				<div
					className={`p-4 rounded-xl space-y-2 max-w-full ${
						isUser ? 'w-fit bg-secondary' : 'w-full min-w-full'
					}`}
				>
					{blocks.map((block, i) =>
						renderBlock(
							block,
							i,
							t,
							(
								toolCall: ToolCallBlock,
								confirm: boolean,
								rules?: ToolCallBlock['suggested_rules'],
							) => {
								onUserConfirm(toolCall, confirm, message.id, rules);
								toolCall.state = confirm ? 'allowed' : 'finished';
							},
						),
					)}
				</div>
			)}
			{showFooter && (
				<div className="flex flex-row items-center text-muted-foreground gap-x-4 px-2 w-full">
					<Badge
						variant="secondary"
						aria-label={isRunning ? t('messageBubble.running') : undefined}
					>
						{isRunning ? (
							<Loader2 data-icon="inline-start" className="animate-spin" />
						) : (
							<CheckCircle data-icon="inline-start" />
						)}
						<span className="tabular-nums tracking-tighter">{elapsedText}</span>
						{hasUsage && (
							<>
								<ArrowUp data-icon="inline-start" className="ml-1" />
								<span className="tabular-nums">
									{formatNumber(message.usage?.input_tokens ?? 0)}
								</span>
								<ArrowDown data-icon="inline-start" className="ml-1" />
								<span className="tabular-nums">
									{formatNumber(message.usage?.output_tokens ?? 0)}
								</span>
							</>
						)}
					</Badge>
				</div>
			)}
		</div>
	);
}
