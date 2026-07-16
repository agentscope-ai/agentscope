import type { ContentBlock, Msg, ToolCallBlock } from '@agentscope-ai/agentscope/message';
import { ArrowDown } from 'lucide-react';
import React from 'react';
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';

import { EmptyMessage } from './Empty';
import { MessageBubble } from '@/components/chat/MessageBubble';
import { TextInput } from '@/components/chat/TextInput.tsx';
import { Button } from '@/components/ui/button.tsx';
import { Spinner } from '@/components/ui/spinner';
import type { ReplyPhase } from '@/hooks/useMessages';
import { cn } from '@/lib/utils';

const LOAD_MORE_THRESHOLD_PX = 100;

interface ChatContentProps {
	msgs: Msg[];
	/**
	 * Reply lifecycle phase from ``useMessages`` — forwarded to
	 * ``TextInput`` so the single send / stop button can pick its
	 * icon, tooltip, disabled state and click handler from one source.
	 */
	phase: ReplyPhase;
	disabled: boolean;
	hasMore: boolean;
	loadingMore: boolean;
	onLoadMore: () => Promise<boolean>;
	onSend: (content: ContentBlock[]) => void;
	onUserConfirm: (
		toolCall: ToolCallBlock,
		confirm: boolean,
		replyId: string,
		rules?: ToolCallBlock['suggested_rules'],
	) => void;
	autoComplete?: (input: string) => string | null;
	className?: string;
	/** Called when the user clicks the stop button. */
	onInterrupt?: () => void;
	/**
	 * Optional content pinned at the bottom of the chat — between the
	 * message scroll area and the text input (e.g. pending subagent HITL
	 * cards on a team leader's view). Rendered below the conversation so
	 * a pending confirmation sits next to the input, where the user is
	 * looking, rather than scrolled off the top.
	 */
	footerSlot?: React.ReactNode;
	/** @see TextInputProps.allowedInputTypes */
	allowedInputTypes: string[];
	/** @see TextInputProps.fileProcessor */
	fileProcessor: (file: File) => Promise<ContentBlock | null>;
}

const ChatContentComponent: React.FC<ChatContentProps> = ({
	msgs,
	phase,
	disabled,
	hasMore,
	loadingMore,
	onLoadMore,
	onSend,
	onUserConfirm,
	autoComplete,
	className,
	onInterrupt,
	footerSlot,
	allowedInputTypes,
	fileProcessor,
}) => {
	const scrollAreaRef = useRef<HTMLDivElement>(null);
	const prevMsgCountRef = useRef<number>(0);
	const prevFirstMsgIdRef = useRef<string | undefined>(undefined);
	const wasNearBottomRef = useRef<boolean>(true);
	const lastScrollTopRef = useRef<number>(0);
	const pendingPrependRef = useRef<{
		firstMessageId: string;
		scrollHeight: number;
		scrollTop: number;
	} | null>(null);
	const [showScrollToBottom, setShowScrollToBottom] = useState(false);

	const updateScrollState = useCallback(() => {
		const scrollArea = scrollAreaRef.current;
		if (!scrollArea) return;

		const { scrollTop, scrollHeight, clientHeight } = scrollArea;
		const distanceFromBottom = scrollHeight - scrollTop - clientHeight;

		wasNearBottomRef.current = distanceFromBottom <= 50;
		setShowScrollToBottom(distanceFromBottom > 100);
	}, []);

	// Restore the viewport after older messages are prepended.
	useLayoutEffect(() => {
		if (msgs.length === 0) {
			pendingPrependRef.current = null;
			lastScrollTopRef.current = 0;
			return;
		}

		const pending = pendingPrependRef.current;
		const scrollArea = scrollAreaRef.current;
		if (!pending || !scrollArea || msgs[0]?.id === pending.firstMessageId) return;

		const heightDelta = scrollArea.scrollHeight - pending.scrollHeight;
		const nextScrollTop = pending.scrollTop + heightDelta;
		lastScrollTopRef.current = nextScrollTop;
		scrollArea.scrollTop = nextScrollTop;
		pendingPrependRef.current = null;
		updateScrollState();
	}, [msgs, updateScrollState]);

	// Auto-scroll to bottom only if user is already near the bottom
	useEffect(() => {
		const currentCount = msgs.length;
		const prevCount = prevMsgCountRef.current;
		const firstMsgId = msgs[0]?.id;
		const prependedMessages =
			prevCount > 0 &&
			currentCount > prevCount &&
			prevFirstMsgIdRef.current !== undefined &&
			firstMsgId !== prevFirstMsgIdRef.current;

		const isActive = phase !== 'idle';
		const isInitialLoad = prevCount === 0 && currentCount > 0;
		const hasRelevantUpdate =
			!prependedMessages &&
			((currentCount > prevCount && prevCount > 0) || (isActive && prevCount > 0));
		const shouldScroll = isInitialLoad || (hasRelevantUpdate && wasNearBottomRef.current);

		if (shouldScroll && scrollAreaRef.current) {
			const { scrollHeight } = scrollAreaRef.current;

			scrollAreaRef.current.scrollTo({
				top: scrollHeight,
				behavior: 'smooth',
			});
		} else {
			updateScrollState();
		}

		prevMsgCountRef.current = currentCount;
		prevFirstMsgIdRef.current = firstMsgId;
	}, [msgs, phase, updateScrollState]);

	// Track the scroll direction and load older messages near the top.
	useEffect(() => {
		const scrollArea = scrollAreaRef.current;
		if (!scrollArea) return;

		const handleScroll = () => {
			const { scrollTop, scrollHeight } = scrollArea;

			const wasScrollingUp = scrollTop < lastScrollTopRef.current;
			lastScrollTopRef.current = scrollTop;
			updateScrollState();
			const firstMessageId = msgs[0]?.id;
			if (
				!wasScrollingUp ||
				scrollTop >= LOAD_MORE_THRESHOLD_PX ||
				!firstMessageId ||
				!hasMore ||
				loadingMore ||
				pendingPrependRef.current
			) {
				return;
			}

			pendingPrependRef.current = {
				firstMessageId,
				scrollHeight,
				scrollTop,
			};
			void onLoadMore().then((loaded) => {
				if (!loaded) pendingPrependRef.current = null;
			});
		};

		scrollArea.addEventListener('scroll', handleScroll);
		return () => scrollArea.removeEventListener('scroll', handleScroll);
	}, [hasMore, loadingMore, msgs, onLoadMore, updateScrollState]);

	return (
		<div className={cn('flex flex-col h-full w-full items-center p-2 gap-4', className)}>
			<div className="relative flex-1 min-h-0 w-full max-w-full">
				<div
					ref={scrollAreaRef}
					className="size-full overflow-auto no-scrollbar overflow-x-hidden"
				>
					<div className="flex flex-col gap-4 size-full max-w-full">
						{msgs.length > 0 ? (
							msgs.map((message) => (
								<MessageBubble
									key={message.id}
									message={message}
									onUserConfirm={onUserConfirm}
								/>
							))
						) : (
							<EmptyMessage />
						)}
					</div>
				</div>
				{loadingMore ? (
					<div
						role="status"
						aria-label="Loading older messages"
						className="pointer-events-none absolute inset-x-0 top-0 z-10 flex h-8 items-center justify-center"
					>
						<Spinner className="text-muted-foreground" />
					</div>
				) : null}
				<Button
					type="button"
					variant="outline"
					size="icon"
					aria-label="Scroll to bottom"
					aria-hidden={!showScrollToBottom}
					tabIndex={showScrollToBottom ? 0 : -1}
					className={cn(
						'absolute bottom-4 left-1/2 z-10 -translate-x-1/2 rounded-full shadow-md transition-all duration-200',
						showScrollToBottom
							? 'translate-y-0 opacity-100'
							: 'pointer-events-none translate-y-2 opacity-0',
					)}
					onClick={() =>
						scrollAreaRef.current?.scrollTo({
							top: scrollAreaRef.current.scrollHeight,
							behavior: 'smooth',
						})
					}
				>
					<ArrowDown />
				</Button>
			</div>
			{footerSlot ? <div className="w-full max-w-full shrink-0">{footerSlot}</div> : null}
			<TextInput
				className="min-w-full max-w-full w-full"
				onSend={onSend}
				disabled={disabled}
				autoComplete={autoComplete}
				allowedInputTypes={allowedInputTypes}
				fileProcessor={fileProcessor}
				phase={phase}
				onInterrupt={onInterrupt}
			/>
		</div>
	);
};

export const ChatContent = React.memo(ChatContentComponent);
