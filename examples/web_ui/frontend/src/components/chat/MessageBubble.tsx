import type {
	ContentBlock,
	Msg,
	ToolCallBlock,
	ToolResultBlock,
} from '@agentscope-ai/agentscope/message';
import { ArrowDown, ArrowUp, Circle, Copy } from 'lucide-react';
import type { ReactNode } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

import { ConfirmCard } from './ConfirmCard';
import { getDisplayName, renderCallArgs, renderResult } from './tool-renderers';
import lineCornerSvg from '@/assets/images/line-corner.svg';
import lineVerticalSvg from '@/assets/images/line-vertical.svg';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { useTranslation } from '@/i18n/useI18n';
import { formatNumber } from '@/utils/common';

// Tool call group, containing multiple tool_call and tool_result pairs
interface ToolCallGroupBlock {
	type: 'tool_call_group';
	id: string;
	groupType: 'read_group' | 'glob_group' | 'grep_group' | 'tool_group';
	calls: Array<{
		call: ToolCallBlock;
		result?: ToolResultBlock;
	}>;
}

// Extend ContentBlock type to include ToolCallGroupBlock
type ExtendedContentBlock = ContentBlock | ToolCallGroupBlock;

/**
 * Combine adjacent tool_call and tool_result in message content into tool_call_group
 * Consecutive Read/Glob/Grep tools will be grouped into their respective groups
 * Other tools will be mixed into tool_group
 * @param content
 * @returns The processed content with tool calls grouped together for better rendering.
 */
function groupToolCalls(content: ContentBlock[]): ExtendedContentBlock[] {
	const result: ExtendedContentBlock[] = [];
	let currentGroup: Array<{ call: ToolCallBlock; result?: ToolResultBlock }> = [];
	let currentGroupType: 'read_group' | 'glob_group' | 'grep_group' | 'tool_group' | null = null;

	const getGroupType = (
		toolName: string,
	): 'read_group' | 'glob_group' | 'grep_group' | 'tool_group' => {
		if (toolName === 'Read') return 'read_group';
		if (toolName === 'Glob') return 'glob_group';
		if (toolName === 'Grep') return 'grep_group';
		return 'tool_group';
	};

	const flushCurrentGroup = () => {
		if (currentGroup.length > 0 && currentGroupType) {
			result.push({
				type: 'tool_call_group',
				id: crypto.randomUUID(),
				groupType: currentGroupType,
				calls: currentGroup,
			});
			currentGroup = [];
			currentGroupType = null;
		}
	};

	for (const block of content) {
		if (block.type === 'tool_call') {
			const toolGroupType = getGroupType(block.name);

			// If it's Read/Glob/Grep and the group type is different from the current one, start a new group
			if (toolGroupType !== 'tool_group' && currentGroupType !== toolGroupType) {
				flushCurrentGroup();
				currentGroupType = toolGroupType;
			} else if (toolGroupType === 'tool_group' && currentGroupType !== 'tool_group') {
				// If it's another tool and the current group is not tool_group, start a new group
				flushCurrentGroup();
				currentGroupType = 'tool_group';
			} else if (!currentGroupType) {
				// If there's no current group yet, set the group type
				currentGroupType = toolGroupType;
			}

			// Collect tool_call
			currentGroup.push({ call: block });
		} else if (block.type === 'tool_result') {
			// Find the corresponding tool_call and add result
			const matchingCall = currentGroup.find((item) => item.call.id === block.id);
			if (matchingCall) {
				matchingCall.result = block;
			} else {
				// If no corresponding call is found, create a new group (this should not happen in theory)
				currentGroup.push({
					call: {
						type: 'tool_call',
						id: block.id,
						name: block.name,
						input: '',
						state: 'pending',
					},
					result: block,
				});
			}
		} else {
			// When encountering a non-tool_call/tool_result block, end the current group
			flushCurrentGroup();
			result.push(block);
		}
	}

	// Process the last group
	flushCurrentGroup();

	return result;
}

/**
 * Get the appropriate line connector image based on position in a list.
 * @param index - The current item index
 * @param total - The total number of items
 * @returns The appropriate line connector image (corner or vertical)
 */
function getLineImage(index: number, total: number): string {
	return index === total - 1 ? lineCornerSvg : lineVerticalSvg;
}

/**
 * The ToolStateIcon component renders an icon representing the state of a tool call based on its states.
 * @param root0
 * @param root0.states
 * @returns A ReactNode representing the icon corresponding to the tool call state.
 */
function ToolStateIcon({ states }: { states: (ToolResultBlock['state'] | undefined)[] }) {
	// If any of the states is 'running' or any one is undefined
	if (states.includes('running') || states.includes(undefined)) {
		return (
			<Circle className="size-2.5 text-muted-foreground fill-muted-foreground animate-pulse shrink-0" />
		);
	}

	// If all the states are 'success', show success;
	if (states.every((state) => state === 'success')) {
		return <Circle className="size-2.5 text-green-500 fill-green-500 shrink-0" />;
	}

	// If any of the states is 'error', show error;
	if (states.some((state) => state === 'error')) {
		return <Circle className="size-2.5 text-red-500 fill-red-500 shrink-0" />;
	}

	// if any of the states is 'interrupted', show interrupted;
	if (states.some((state) => state === 'interrupted')) {
		return <Circle className="size-2.5 text-yellow-500 fill-yellow-500 shrink-0" />;
	}

	// Return a default icon if none of the above conditions are met
	return <Circle className="size-2.5 text-muted-foreground fill-muted-foreground shrink-0" />;
}

/**
 * Renders a grouped list of tool calls (Read / Glob / Grep) with a header and indented items.
 * @param root0 - The component props
 * @param root0.calls - Array of tool calls with their results
 * @param root0.label - The label to display for the group
 * @param root0.paramKey - The parameter key to extract from each call
 * @param root0.inline - Whether to display items inline or stacked
 * @returns A ReactNode representing the grouped tool call list
 */
function ToolCallGroupList({
	calls,
	label,
	inline,
}: {
	calls: Array<{ call: ToolCallBlock; result?: ToolResultBlock }>;
	label: ReactNode;
	inline?: boolean;
}) {
	return (
		<div className="flex flex-col w-full">
			<div className="flex flex-row gap-x-2 w-full max-w-full items-center">
				<ToolStateIcon states={calls.map((item) => item.result?.state)} />
				{label}
			</div>
			<div className={`flex ${inline ? 'flex-row' : 'flex-col'} gap-x-2 pl-6 max-w-full`}>
				{calls.map((item, index) => (
					<div
						key={index}
						className="flex flex-row gap-x-2 w-full max-w-full items-stretch"
					>
						<div className="flex-shrink-0 h-full items-center">
							<img
								src={getLineImage(index, calls.length)}
								alt=""
								className="w-3 h-full"
							/>
						</div>
						<div className="truncate flex-1 min-w-0 text-sm">
							{renderCallArgs(item.call, () => '')}
						</div>
					</div>
				))}
			</div>
		</div>
	);
}

/**
 * The RenderToolCallGroup component renders a group of tool calls and their results in a structured format.
 *
 * @param root0
 * @param root0.block
 * @param root0.index
 * @param root0.onUserConfirm
 * @returns A ReactNode representing the rendered tool call group.
 */
function ToolCallGroup({
	block,
	index,
	onUserConfirm,
}: {
	block: ToolCallGroupBlock;
	index: number;
	onUserConfirm?: (
		toolCallBlock: ToolCallBlock,
		confirm: boolean,
		rules?: ToolCallBlock['suggested_rules'],
	) => void;
}): ReactNode {
	const { t } = useTranslation();
	if (block.calls.length === 0) return null;

	const firstNeedConfirm = block.calls.findIndex((item) => item.call.state === 'asking');

	const renderToolCalls =
		firstNeedConfirm === -1 ? block.calls : block.calls.slice(0, firstNeedConfirm + 1);

	const elements: ReactNode[] = [];

	if (block.groupType === 'read_group') {
		elements.push(
			<ToolCallGroupList
				key="read"
				calls={renderToolCalls}
				label={
					<span className="text-sm">
						<strong className="truncate text-primary">{t('tool.read.name')} </strong>
						{t('tool.read.fileCount', { count: renderToolCalls.length })}
					</span>
				}
			/>,
		);
	} else if (block.groupType === 'glob_group' || block.groupType === 'grep_group') {
		elements.push(
			<ToolCallGroupList
				key="search"
				calls={renderToolCalls}
				inline
				label={
					<strong className="truncate text-primary text-sm">
						{block.groupType === 'glob_group'
							? t('tool.glob.name')
							: t('tool.grep.name')}
					</strong>
				}
			/>,
		);
	} else {
		for (const { call, result } of renderToolCalls) {
			const displayName = getDisplayName(call, t);
			const args = renderCallArgs(call, t);
			const resultContent = result ? renderResult(call, result, t) : null;

			elements.push(
				<div className="flex flex-col w-full max-w-full text-sm">
					<div className="flex flex-row gap-x-2 w-full max-w-full items-center">
						<ToolStateIcon states={[result?.state]} />
						<span className="truncate">
							<strong className="truncate text-primary">{displayName}</strong>
							{args && <>({args})</>}
						</span>
					</div>
					{resultContent && (
						<div className="flex flex-row gap-x-2 pl-6 max-w-full">
							<div className="flex-shrink-0">
								<img src={lineCornerSvg} alt="" className="w-3 h-4" />
							</div>
							{resultContent}
						</div>
					)}
				</div>,
			);
		}
	}

	// Need to confirm
	if (firstNeedConfirm !== -1) {
		const { call } = block.calls[firstNeedConfirm];
		elements.push(
			<ConfirmCard
				toolCall={call}
				onUserConfirm={(confirm, rules) => {
					if (onUserConfirm) onUserConfirm(call, confirm, rules);
				}}
			/>,
		);
	}

	return (
		<div key={index} className="flex flex-col gap-y-4 text-muted-foreground">
			{elements}
		</div>
	);
}

/**
 * Renders a content block based on its type.
 *
 * @param block - The content block to render.
 * @param index - The index of the block in the content array.
 * @param onUserConfirm
 * @returns A React element representing the rendered block.
 */
function renderBlock(
	block: ExtendedContentBlock,
	index: number,
	onUserConfirm?: (
		toolCallBlock: ToolCallBlock,
		confirm: boolean,
		rules?: ToolCallBlock['suggested_rules'],
	) => void,
	t?: (key: string) => string,
) {
	switch (block.type) {
		case 'tool_call_group':
			return <ToolCallGroup block={block} index={index} onUserConfirm={onUserConfirm} />;
		case 'text':
			return (
				<div className="prose text-sm w-full min-w-full">
					<ReactMarkdown
						remarkPlugins={[remarkGfm]}
						// rehypePlugins={[rehypeRaw]}
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
						{t ? t('messageBubble.thinking') : 'Thinking'}
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
					return <img src={data} alt="Uploaded image" />;
				case 'audio':
					return <audio controls src={data} />;
				case 'video':
					return <video controls src={data} />;
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
 * @param root0 - The component props.
 * @param root0.message - The message object to display.
 * @param root0.onUserConfirm
 * @returns A MessageBubble component.
 */
export function MessageBubble({ message, onUserConfirm }: MessageBubbleProps) {
	const isUser = message.role === 'user';
	const { t } = useTranslation();

	const renderContent = () => {
		if (typeof message.content === 'string') {
			return <p className="whitespace-pre-wrap">{message.content}</p>;
		}
		// Combine adjacent tool_call and tool_result into tool_call_group
		const processedContent = groupToolCalls(message.content);
		return processedContent.map((block, i) =>
			renderBlock(
				block,
				i,
				(
					toolCall: ToolCallBlock,
					confirm: boolean,
					rules?: ToolCallBlock['suggested_rules'],
				) => {
					onUserConfirm(toolCall, confirm, message.id, rules);
					toolCall.state = confirm ? 'allowed' : 'finished';
					// Remove confirmation UI while preserving denied state
				},
				t,
			),
		);
	};

	return (
		<div
			className={`flex flex-col w-full max-w-full ${isUser ? 'items-end' : 'items-start'} mb-4`}
		>
			<div
				className={`p-4 rounded-xl space-y-2 max-w-full ${isUser ? 'w-fit bg-secondary' : 'w-full min-w-full'}`}
			>
				{renderContent()}
			</div>
			{isUser ? null : (
				<div className="flex flex-row text-muted-foreground gap-x-4 px-2">
					<Badge variant="secondary">
						<ArrowUp data-icon="inline-start" />
						{formatNumber(message.usage?.inputTokens || 0)}
						<ArrowDown data-icon="inline-start" className="ml-1" />
						{formatNumber(message.usage?.outputTokens || 0)}
					</Badge>
				</div>
			)}
		</div>
	);
}
