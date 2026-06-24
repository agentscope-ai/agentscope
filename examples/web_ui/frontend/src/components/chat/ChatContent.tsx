import type { ContentBlock, Msg, ToolCallBlock } from '@agentscope-ai/agentscope/message';
import React from 'react';
import { useRef, useEffect } from 'react';

import { EmptyMessage } from './Empty';
import { MessageBubble } from '@/components/chat/MessageBubble';
import { TextInput } from '@/components/chat/TextInput.tsx';
import { cn } from '@/lib/utils';

interface ChatContentProps {
	msgs: Msg[];
	sending: boolean;
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
	sending,
	disabled,
	onSend,
	onUserConfirm,
	autoComplete,
	className,
	footerSlot,
	allowedInputTypes,
	fileProcessor,
}) => {
	const scrollAreaRef = useRef<HTMLDivElement>(null);
	const prevMsgCountRef = useRef<number>(0);
	const wasNearBottomRef = useRef<boolean>(true);

	// Auto-scroll to bottom only if user is already near the bottom
	useEffect(() => {
		const currentCount = msgs.length;
		const prevCount = prevMsgCountRef.current;

		const shouldCheck =
			(currentCount > prevCount && prevCount > 0) || (sending && prevCount > 0);

		if (shouldCheck && scrollAreaRef.current) {
			const { scrollHeight } = scrollAreaRef.current;

			// Check if user was near bottom before content changed
			const isNearBottom = wasNearBottomRef.current;

			if (isNearBottom) {
				scrollAreaRef.current.scrollTo({
					top: scrollHeight,
					behavior: 'smooth',
				});
			}
		}

		prevMsgCountRef.current = currentCount;
	}, [msgs, sending]);

	// Track if user is near bottom whenever they scroll
	useEffect(() => {
		const scrollArea = scrollAreaRef.current;
		if (!scrollArea) return;

		const handleScroll = () => {
			const { scrollTop, scrollHeight, clientHeight } = scrollArea;
			wasNearBottomRef.current = scrollTop + clientHeight >= scrollHeight - 50;
		};

		scrollArea.addEventListener('scroll', handleScroll);
		return () => scrollArea.removeEventListener('scroll', handleScroll);
	}, []);

	return (
		<div className={cn('flex flex-col h-full w-full items-center p-2 gap-4', className)}>
			<div
				ref={scrollAreaRef}
				className="flex-1 w-full max-w-full overflow-auto no-scrollbar overflow-x-hidden"
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
			{footerSlot ? <div className="w-full max-w-full shrink-0">{footerSlot}</div> : null}
			<TextInput
				className="min-w-full max-w-full w-full"
				onSend={onSend}
				disabled={disabled}
				autoComplete={autoComplete}
				allowedInputTypes={allowedInputTypes}
				fileProcessor={fileProcessor}
			/>
		</div>
	);
};

export const ChatContent = React.memo(ChatContentComponent);
