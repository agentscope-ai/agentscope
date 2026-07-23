import type { ContentBlock, Msg, ToolCallBlock } from '@agentscope-ai/agentscope/message';
import { ArrowDown } from 'lucide-react';
import React from 'react';
import { useCallback, useEffect, useRef, useState } from 'react';

import { MessageBubble } from '@/components/chat/MessageBubble';
import { TextInput } from '@/components/chat/TextInput.tsx';
import { Button } from '@/components/ui/button.tsx';
import type { ReplyPhase } from '@/hooks/useMessages';
import { useTranslation } from '@/i18n/useI18n';
import { cn } from '@/lib/utils';

interface ChatContentProps {
	msgs: Msg[];
	/**
	 * Reply lifecycle phase from ``useMessages`` — forwarded to
	 * ``TextInput`` so the single send / stop button can pick its
	 * icon, tooltip, disabled state and click handler from one source.
	 */
	phase: ReplyPhase;
	disabled: boolean;
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
	onSend,
	onUserConfirm,
	autoComplete,
	className,
	onInterrupt,
	footerSlot,
	allowedInputTypes,
	fileProcessor,
}) => {
	const { t } = useTranslation();
	const scrollAreaRef = useRef<HTMLDivElement>(null);
	const prevMsgCountRef = useRef<number>(0);
	const wasNearBottomRef = useRef<boolean>(true);
	const isEmpty = msgs.length === 0;
	const [showScrollToBottom, setShowScrollToBottom] = useState(false);

	const updateScrollState = useCallback(() => {
		const scrollArea = scrollAreaRef.current;
		if (!scrollArea) return;

		const { scrollTop, scrollHeight, clientHeight } = scrollArea;
		const distanceFromBottom = scrollHeight - scrollTop - clientHeight;

		wasNearBottomRef.current = distanceFromBottom <= 50;
		setShowScrollToBottom(distanceFromBottom > 100);
	}, []);

	// Auto-scroll to bottom only if user is already near the bottom
	useEffect(() => {
		const currentCount = msgs.length;
		const prevCount = prevMsgCountRef.current;

		const isActive = phase !== 'idle';
		const isInitialLoad = prevCount === 0 && currentCount > 0;
		const hasRelevantUpdate =
			(currentCount > prevCount && prevCount > 0) || (isActive && prevCount > 0);
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
	}, [msgs, phase, updateScrollState]);

	// Track if user is near bottom whenever they scroll
	useEffect(() => {
		const scrollArea = scrollAreaRef.current;
		if (!scrollArea) return;

		scrollArea.addEventListener('scroll', updateScrollState);
		return () => scrollArea.removeEventListener('scroll', updateScrollState);
	}, [updateScrollState]);

	// On an empty session the prompt and the input centre together, so every box
	// down to the message list shrinks to its content instead of filling.
	return (
		<div
			className={cn(
				'flex flex-col h-full w-full items-center p-2 gap-4',
				isEmpty && 'justify-center',
				className,
			)}
		>
			<div
				className={cn(
					'relative min-h-0 w-full max-w-full',
					isEmpty ? 'flex-none' : 'flex-1',
				)}
			>
				<div
					ref={scrollAreaRef}
					className={cn(
						'overflow-auto no-scrollbar overflow-x-hidden',
						isEmpty ? 'w-full' : 'size-full',
					)}
				>
					<div
						className={cn(
							'flex flex-col gap-4 max-w-full',
							isEmpty ? 'w-full' : 'size-full',
						)}
					>
						{isEmpty ? (
							<p className="text-center text-lg mb-2">{t('chat.greeting')}</p>
						) : (
							msgs.map((message) => (
								<MessageBubble
									key={message.id}
									message={message}
									onUserConfirm={onUserConfirm}
								/>
							))
						)}
					</div>
				</div>
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
