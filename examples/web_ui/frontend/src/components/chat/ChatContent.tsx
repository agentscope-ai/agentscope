import {
	type ContentBlock,
	getContentBlocks,
	type Msg,
	type ToolCallBlock,
} from '@agentscope-ai/agentscope/message';
import { GitBranch } from 'lucide-react';
import React, { useEffect, useMemo, useState } from 'react';

import { Button } from '../ui/button';
import { DiffStats } from './tool-renderers/_shared';
import type { GitStatus } from '@/api';
import { ASMessageBubble } from '@/components/chat/ASMessageBubble.tsx';
import { ConfirmCard } from '@/components/chat/ConfirmCard.tsx';
import { FlipCard } from '@/components/chat/FlipCard.tsx';
import { TextInput } from '@/components/chat/TextInput.tsx';
import { WorkingDirectoryDialog } from '@/components/dialog/WorkingDirectoryDialog';
import {
	MessageScroller,
	MessageScrollerButton,
	MessageScrollerContent,
	MessageScrollerItem,
	MessageScrollerProvider,
	MessageScrollerViewport,
} from '@/components/ui/message-scroller.tsx';
import { Spinner } from '@/components/ui/spinner';
import type { ReplyPhase } from '@/hooks/useMessages';
import { useTranslation } from '@/i18n/useI18n';
import { cn } from '@/lib/utils';

/** How long a load may run before it is worth showing a spinner. */
const SPINNER_DELAY_MS = 150;

interface ChatContentProps {
	msgs: Msg[];
	/**
	 * Whether the history for the currently selected session is still on
	 * the wire. Takes precedence over {@link msgs}, which still holds the
	 * *previous* session's messages until the fetch effect clears them.
	 */
	loading?: boolean;
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
	) => Promise<void>;
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
	/** Agent owning the open session — drives the directory picker. */
	agentId: string | null;
	/** The open session, whose working directory the picker edits. */
	sessionId: string | null;
	/** Current working directory, relative to the workspace root. */
	cwd: string | null;
	/** Persists a new working directory. */
	onCwdChange: (cwd: string | null) => void | Promise<void>;
	/** Git state of {@link cwd}; `null` hides the branch badge entirely. */
	git?: GitStatus | null;
	/** Re-reads the git state, since nothing polls for it. */
	onRefreshGit?: () => void | Promise<void>;
}

const ChatContentComponent: React.FC<ChatContentProps> = ({
	msgs,
	loading = false,
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
	agentId,
	sessionId,
	cwd,
	onCwdChange,
	git,
	onRefreshGit,
}) => {
	const { t } = useTranslation();
	// Only a session that finished loading with nothing in it is empty.
	// Treating "no messages yet" as empty would flash the greeting over
	// every session that does have history.
	const isEmpty = !loading && msgs.length === 0;

	// A spinner that appears and vanishes inside a couple of frames reads
	// as a flicker, not as feedback — so hold it back until the load has
	// gone on long enough to be worth reporting. The gap renders blank,
	// which is invisible at this duration.
	const [showSpinner, setShowSpinner] = useState(false);
	useEffect(() => {
		if (!loading) {
			setShowSpinner(false);
			return;
		}
		const timer = setTimeout(() => setShowSpinner(true), SPINNER_DELAY_MS);
		return () => clearTimeout(timer);
	}, [loading]);

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
			{loading ? (
				<div className="flex flex-1 w-full items-center justify-center">
					{showSpinner ? <Spinner className="size-5 text-muted-foreground" /> : null}
				</div>
			) : isEmpty ? (
				<span className="text-center text-4xl font-light tracking-[-0.035em] text-foreground mb-2">
					{t('chat.greeting')}
				</span>
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

			{loading ? null : (
				<div className="relative min-w-full max-w-full w-full">
					<FlipCard
						visible={toConfirmedToolCalls.length > 0 || footerSlot !== null}
						className="absolute bottom-full left-0 right-0 mb-2 z-50"
					>
						{toConfirmedToolCalls.length > 0 ? (
							<ConfirmCard
								key={`${toConfirmedToolCalls[0].replyId}:${toConfirmedToolCalls[0].toolCall.id}`}
								toolCall={toConfirmedToolCalls[0].toolCall}
								onUserConfirm={(confirm, rules) =>
									onUserConfirm(
										toConfirmedToolCalls[0].toolCall,
										confirm,
										toConfirmedToolCalls[0].replyId,
										rules,
									)
								}
							/>
						) : (
							footerSlot
						)}
					</FlipCard>
					{/* Concentric radii: the pill stays rounded-[28px] on all four
				    corners and floats on this surface, whose own radius is
				    28 + the 4px padding. Matching them is what keeps the two
				    edges parallel — a plain rectangle here would show the
				    pill's corners cutting into it. */}
					<TextInput
						className="min-w-full max-w-full w-full rounded-[32px] bg-muted p-1"
						onSend={onSend}
						disabled={disabled}
						autoComplete={autoComplete}
						allowedInputTypes={allowedInputTypes}
						fileProcessor={fileProcessor}
						phase={phase}
						onInterrupt={onInterrupt}
						headerSlot={
							<div className="flex w-full items-center justify-between px-2 py-1 text-sm text-muted-foreground">
								<WorkingDirectoryDialog
									agentId={agentId}
									sessionId={sessionId}
									value={cwd}
									onChange={onCwdChange}
								/>
								{git && (
									<Button
										className="font-mono"
										variant="secondary"
										size="sm"
										onClick={() => void onRefreshGit?.()}
										title={t('workdir.gitTooltip', {
											staged: git.staged,
											unstaged: git.unstaged,
											untracked: git.untracked,
										})}
									>
										<GitBranch />
										{/* A detached HEAD has no branch to name, so
										    fall back to the commit it sits on. The
										    server sends no git at all when it has
										    neither. */}
										{git.branch ?? git.head?.slice(0, 7)}
										{git.ahead !== null && git.ahead > 0 && (
											<span className="text-xs">↑{git.ahead}</span>
										)}
										{git.behind !== null && git.behind > 0 && (
											<span className="text-xs">↓{git.behind}</span>
										)}
										{/* Renders nothing when both are zero, so a
										    clean tree shows just the branch. */}
										<DiffStats
											className="text-xs font-mono"
											insertions={git.insertions}
											deletions={git.deletions}
										/>
									</Button>
								)}
							</div>
						}
					/>
				</div>
			)}
		</div>
	);
};

export const ChatContent = React.memo(ChatContentComponent);
