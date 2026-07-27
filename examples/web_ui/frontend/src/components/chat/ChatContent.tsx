import {
	type ContentBlock,
	getContentBlocks,
	type Msg,
	type ToolCallBlock,
} from '@agentscope-ai/agentscope/message';
import React, { useMemo } from 'react';

import { ASMessageBubble } from '@/components/chat/ASMessageBubble.tsx';
import { ConfirmCard } from '@/components/chat/ConfirmCard.tsx';
import { FlipCard } from '@/components/chat/FlipCard.tsx';
import { TextInput } from '@/components/chat/TextInput.tsx';
import {
	MessageScroller,
	MessageScrollerButton,
	MessageScrollerContent,
	MessageScrollerItem,
	MessageScrollerProvider,
	MessageScrollerViewport,
} from '@/components/ui/message-scroller.tsx';
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
	const isEmpty = msgs.length === 0;

	const toConfirmedToolCalls = useMemo(() => {
		if (msgs.length === 0) return [];

		const lastMsg = msgs[msgs.length - 1];
		return getContentBlocks(lastMsg, 'tool_call')
			.filter((tc) => tc.state === 'asking')
			.map((tc) => ({ replyId: lastMsg.id, toolCall: tc }));
	}, [msgs]);

	// On an empty session the prompt and the input centre together, so every box
	// down to the message list shrinks to its content instead of filling.
	return (
		<div
			className={cn(
				'flex flex-col h-full w-full items-center gap-4',
				isEmpty && 'justify-center',
				className,
			)}
		>
			{isEmpty ? (
				<p className="text-center text-lg mb-2">{t('chat.greeting')}</p>
			) : (
				<MessageScrollerProvider autoScroll={true} defaultScrollPosition={'end'}>
					<MessageScroller>
						<MessageScrollerViewport>
							<MessageScrollerContent>
								{msgs.map((message) => (
									<MessageScrollerItem key={message.id} messageId={message.id}>
										<ASMessageBubble
											key={message.id}
											message={message}
											onUserConfirm={onUserConfirm}
										/>
									</MessageScrollerItem>
								))}
							</MessageScrollerContent>
						</MessageScrollerViewport>
						<MessageScrollerButton className="rounded-full" />
					</MessageScroller>
				</MessageScrollerProvider>
			)}

			<div className="relative min-w-full max-w-full w-full">
				<FlipCard
					visible={toConfirmedToolCalls.length > 0 || footerSlot !== null}
					className="absolute bottom-full left-0 right-0 mb-2 z-50"
				>
					{toConfirmedToolCalls.length > 0 ? (
						<ConfirmCard
							toolCall={toConfirmedToolCalls[0].toolCall}
							onUserConfirm={async (confirm, rules) => {
								onUserConfirm(
									toConfirmedToolCalls[0].toolCall,
									confirm,
									toConfirmedToolCalls[0].replyId,
									rules,
								);
							}}
						/>
					) : (
						footerSlot
					)}
				</FlipCard>
				<TextInput
					className="min-w-full max-w-full w-full"
					onSend={onSend}
					disabled={disabled}
					autoComplete={autoComplete}
					allowedInputTypes={allowedInputTypes}
					fileProcessor={fileProcessor}
					phase={phase}
					onInterrupt={onInterrupt}
				/>{' '}
			</div>
		</div>
	);
};

export const ChatContent = React.memo(ChatContentComponent);
