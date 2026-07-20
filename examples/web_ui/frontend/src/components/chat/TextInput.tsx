import type { ContentBlock, TextBlock } from '@agentscope-ai/agentscope/message';
import { Paperclip, Send, Loader2, Square, X, type LucideIcon } from 'lucide-react';
import React, {
	useState,
	useRef,
	useMemo,
	useLayoutEffect,
	type KeyboardEvent,
	useImperativeHandle,
	forwardRef,
} from 'react';

import { Button } from '../ui/button';
import { Kbd } from '../ui/kbd';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import type { ReplyPhase } from '@/hooks/useMessages';
import { useTranslation } from '@/i18n/useI18n.ts';
import { cn } from '@/lib/utils';

/**
 * Represents a file that has been selected and processed (or is being processed).
 */
interface ProcessedFile {
	/** Original file name for display */
	name: string;
	/** Processing status */
	status: 'processing' | 'done';
	/** The resulting ContentBlock after processing (available when status === 'done') */
	block: ContentBlock | null;
}

interface TextInputProps {
	onSend: (blocks: ContentBlock[]) => void;
	placeholder?: string;
	autoComplete?: (input: string) => string | null;
	disabled?: boolean;
	className?: string;
	/**
	 * Controls which file types the file picker accepts.
	 * Uses standard MIME types and file extensions, e.g.:
	 *   - Images:    "image/*" or "image/jpeg", "image/png"
	 *   - Audio:     "audio/*" or "audio/mpeg", "audio/wav"
	 *   - Video:     "video/*"
	 *   - Plain text:"text/plain"
	 *   - PDF:       "application/pdf"
	 *   - Word:      ".doc,.docx,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
	 *   - Excel:     ".xls,.xlsx,application/vnd.ms-excel,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
	 *
	 * When undefined → no restriction (all files allowed).
	 * When empty array [] → attachment button is disabled (model accepts no files).
	 */
	allowedInputTypes?: string[];
	/**
	 * Called immediately when a file is selected (at attach time, NOT at send time).
	 * Should resolve to a ContentBlock to include in the message, or null to skip the file.
	 * Runs concurrently for all selected files; the UI shows a loading state per file while processing.
	 */
	fileProcessor: (file: File) => Promise<ContentBlock | null>;
	/**
	 * The current reply lifecycle phase from ``useMessages``. Drives the
	 * send / stop button in one shot:
	 *   - ``idle`` — Send (enabled when there is content to send)
	 *   - ``streaming`` — Stop (click to interrupt)
	 *   - ``interrupting`` — Stop (disabled while the interrupt is in flight)
	 */
	phase?: ReplyPhase;
	onInterrupt?: () => void;
}

export interface TextInputRef {
	focus: () => void;
}

/** One line box of textarea text: ``text-sm`` (14px) at a 1.5 line-height. */
const LINE_HEIGHT_PX = 21;
/** Height of the input in its collapsed, single-line state. */
const COLLAPSED_HEIGHT_PX = 52;
/**
 * Padding rather than height: a textarea top-aligns its text, so forcing the
 * height would leave dead space under the caret instead of centring the line.
 */
const TEXTAREA_PADDING_Y_PX = (COLLAPSED_HEIGHT_PX - LINE_HEIGHT_PX) / 2;
/** Growth stops after six lines of text; the textarea scrolls beyond that. */
const MAX_HEIGHT_PX = LINE_HEIGHT_PX * 6 + TEXTAREA_PADDING_Y_PX * 2;
/** Horizontal padding of the textarea, mirrored by the overlay and the ghost. */
const TEXTAREA_PADDING_X_PX = 12;

/**
 * A text input component with file attachment support and autocomplete functionality.
 *
 * @param root0 - The component props.
 * @param root0.onSend - Callback function to handle sending content blocks.
 * @param root0.placeholder - Placeholder text for the input field.
 * @param root0.autoComplete - Function to provide autocomplete suggestions.
 * @param root0.disabled - Whether the input is disabled.
 * @param root0.className - Additional CSS classes for styling.
 * @returns A TextInput component.
 */
export const TextInput = forwardRef<TextInputRef, TextInputProps>(
	(
		{
			onSend,
			placeholder,
			autoComplete,
			disabled = false,
			className,
			allowedInputTypes,
			fileProcessor,
			phase = 'idle',
			onInterrupt,
		},
		ref,
	) => {
		const { t } = useTranslation();
		const defaultPlaceholder = placeholder || t('chat.inputPlaceholder');
		const [value, setValue] = useState('');
		const [files, setFiles] = useState<ProcessedFile[]>([]);
		const [isFocused, setIsFocused] = useState(false);
		const textareaRef = useRef<HTMLTextAreaElement>(null);
		const fileInputRef = useRef<HTMLInputElement>(null);
		const measureRef = useRef<HTMLSpanElement>(null);
		/** ``true`` — textarea takes the full width, buttons drop to their own row. */
		const [isStacked, setIsStacked] = useState(false);

		// Derive the accept attribute for the hidden file input
		const acceptAttr =
			allowedInputTypes && allowedInputTypes.length > 0
				? allowedInputTypes.join(',')
				: undefined;

		// Attachment button is disabled when the model explicitly accepts no file types
		const attachDisabled =
			disabled || (allowedInputTypes !== undefined && allowedInputTypes.length === 0);

		// Whether any file is still being processed (block send until all done)
		const hasProcessing = files.some((f) => f.status === 'processing');

		useImperativeHandle(ref, () => ({
			focus: () => textareaRef.current?.focus(),
		}));

		// Grow the textarea with its content. The ``auto`` reset is what lets it
		// shrink again — ``scrollHeight`` never reports less than the current height.
		useLayoutEffect(() => {
			const textarea = textareaRef.current;
			if (!textarea) return;
			textarea.style.height = 'auto';
			textarea.style.height = `${textarea.scrollHeight}px`;
			// Stacking widens the textarea, so the line count has to be redone.
		}, [value, isStacked]);

		// Measured on the ghost, not the textarea: stacking widens the textarea, so
		// measuring it would flip-flop (wraps → stack → fits → unstack → wraps).
		useLayoutEffect(() => {
			const ghost = measureRef.current;
			if (!ghost) return;
			setIsStacked(ghost.scrollHeight > LINE_HEIGHT_PX);
		}, [value]);

		// Calculate autocomplete suggestion using useMemo
		const suggestion = useMemo(() => {
			if (autoComplete && value && isFocused) {
				const result = autoComplete(value);
				// Only return the part after the cursor
				if (result && result.startsWith(value)) {
					return result.substring(value.length);
				}
				return result || '';
			}
			return '';
		}, [value, autoComplete, isFocused]);

		const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
			// Tab key to select autocomplete
			if (e.key === 'Tab' && suggestion) {
				e.preventDefault();
				setValue(value + suggestion);
				return;
			}

			// Enter to send message, Shift+Enter for new line
			if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
				e.preventDefault();
				handleSend();
			}
		};

		const handleSend = () => {
			if (!value.trim() || disabled || hasProcessing) return;

			const blocks: ContentBlock[] = [];

			// Add text block
			if (value.trim()) {
				const textBlock: TextBlock = {
					id: crypto.randomUUID(),
					type: 'text',
					text: value.trim(),
				};
				blocks.push(textBlock);
			}

			// Add processed file blocks (skip errored ones)
			files.forEach((f) => {
				if (f.status === 'done' && f.block) {
					blocks.push(f.block);
				}
			});

			onSend?.(blocks);
			setValue('');
			setFiles([]);
		};

		/**
		 * Send / stop button configuration derived from the current reply
		 * phase. One struct = one branch of rendering, so the JSX stays flat.
		 */
		const sendButton: {
			icon: LucideIcon;
			tooltip: string;
			disabled: boolean;
			onClick: (() => void) | undefined;
		} = (() => {
			if (phase === 'streaming') {
				return {
					icon: Square,
					tooltip: t('textInput.stop'),
					disabled: false,
					onClick: onInterrupt,
				};
			}
			if (phase === 'interrupting') {
				return {
					icon: Square,
					tooltip: t('textInput.stopping'),
					disabled: true,
					onClick: onInterrupt,
				};
			}
			return {
				icon: Send,
				tooltip: t('textInput.send'),
				disabled: disabled || !value.trim() || hasProcessing,
				onClick: handleSend,
			};
		})();

		const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
			if (!e.target.files) return;
			const selected = Array.from(e.target.files);
			// Reset input value so the same file can be re-selected
			e.target.value = '';

			selected.forEach((file) => {
				// Insert a placeholder in processing state
				const placeholder: ProcessedFile = {
					name: file.name,
					status: 'processing',
					block: null,
				};

				setFiles((prev) => [...prev, placeholder]);

				fileProcessor(file)
					.then((block) => {
						setFiles(
							(prev) =>
								prev
									.map((f) =>
										f.name === file.name && f.status === 'processing'
											? block
												? { ...f, status: 'done', block }
												: null
											: f,
									)
									.filter(Boolean) as ProcessedFile[],
						);
					})
					.catch(() => {
						// Caller is responsible for error notification (e.g. toast).
						// Just silently remove the entry here.
						setFiles((prev) =>
							prev.filter(
								(f) => !(f.name === file.name && f.status === 'processing'),
							),
						);
					});
			});
		};

		return (
			<div className={cn('flex flex-col gap-1', className)}>
				<div
					id="tour-chat-input"
					className="flex w-full flex-col gap-2 rounded-[28px] border bg-background px-2"
					data-tour="chat-input"
				>
					{/* File list */}
					{files.length > 0 && (
						<div className="flex flex-wrap gap-2">
							{files.map((file, index) => (
								<div
									key={index}
									className="flex items-center gap-1 rounded bg-muted px-2 py-1 text-sm"
								>
									{file.status === 'processing' && (
										<Loader2 className="h-3 w-3 shrink-0 animate-spin text-muted-foreground" />
									)}
									<span className="max-w-[200px] truncate">{file.name}</span>
									<button
										onClick={() =>
											setFiles(files.filter((_, i) => i !== index))
										}
										className="text-muted-foreground hover:text-foreground"
									>
										<X className="h-3 w-3" />
									</button>
								</div>
							))}
						</div>
					)}

					{/* ``items-end`` in both layouts: the buttons are then already at the
					    bottom before stacking moves them there, so nothing jumps. */}
					<div className="relative flex flex-wrap items-end justify-end">
						{/* Ghost row, always laid out side-by-side, so the width it hands
						    the text is the narrow one whichever layout is on screen. */}
						<div
							aria-hidden
							className="pointer-events-none invisible absolute inset-x-0 top-0 flex h-0 items-start overflow-hidden"
						>
							<div className="min-w-0 flex-1">
								{/* Padding-x and line-height decide where text wraps, so they
								    match the textarea; padding-y is left off on purpose. */}
								<span
									ref={measureRef}
									className="block text-sm"
									style={{
										paddingLeft: `${TEXTAREA_PADDING_X_PX}px`,
										paddingRight: `${TEXTAREA_PADDING_X_PX}px`,
										lineHeight: `${LINE_HEIGHT_PX}px`,
										whiteSpace: 'pre-wrap',
										wordWrap: 'break-word',
									}}
								>
									{value}
								</span>
							</div>
							{/* Stands in for the button cluster below — keep the count, the
							    gap and the ``size-9`` footprint in step with it. */}
							<div className="flex shrink-0 gap-2">
								<div className="size-9" />
								<div className="size-9" />
							</div>
						</div>

						{/* ``min-w-0`` lets the textarea shrink instead of pushing the
						    buttons out of the row once the text gets long. */}
						<div
							className={cn('relative min-w-0', isStacked ? 'basis-full' : 'flex-1')}
						>
							{/* ``block`` — inline-block would sit on the text baseline and
							    leave a descender gap that makes the wrapper taller. */}
							<textarea
								ref={textareaRef}
								value={value}
								onChange={(e) => setValue(e.target.value)}
								onKeyDown={handleKeyDown}
								onFocus={() => setIsFocused(true)}
								onBlur={() => setIsFocused(false)}
								placeholder={defaultPlaceholder}
								disabled={disabled}
								rows={1}
								className="block w-full resize-none rounded-md border-0 bg-transparent px-3 text-sm outline-none placeholder:text-muted-foreground focus:outline-none disabled:cursor-not-allowed disabled:opacity-50"
								style={{
									minHeight: `${COLLAPSED_HEIGHT_PX}px`,
									maxHeight: `${MAX_HEIGHT_PX}px`,
									lineHeight: `${LINE_HEIGHT_PX}px`,
									paddingTop: `${TEXTAREA_PADDING_Y_PX}px`,
									paddingBottom: `${TEXTAREA_PADDING_Y_PX}px`,
									overflowY: 'auto',
								}}
								autoFocus={true}
							/>

							{/* Autocomplete overlay — its padding and line-height mirror the
							    textarea's, or the suggestion drifts off the real text. */}
							{suggestion && isFocused && (
								<div
									className="pointer-events-none absolute left-0 top-0 px-3 text-sm"
									style={{
										lineHeight: `${LINE_HEIGHT_PX}px`,
										paddingTop: `${TEXTAREA_PADDING_Y_PX}px`,
										paddingBottom: `${TEXTAREA_PADDING_Y_PX}px`,
										whiteSpace: 'pre-wrap',
										wordWrap: 'break-word',
									}}
								>
									{/* Invisible input text */}
									<span className="invisible">{value}</span>
									{/* Suggestion text */}
									<span className="text-muted-foreground">{suggestion}</span>
									{/* Tab hint */}
									<span className="ml-2 text-xs text-muted-foreground/60">
										<Kbd>Tab</Kbd> {t('textInput.toComplete')}
									</span>
								</div>
							)}
						</div>

						{/* Collapsed-height box centring the buttons, so a single line still
						    reads as centred while the row bottom-aligns them. */}
						<div
							className="flex shrink-0 items-center gap-2"
							style={{ height: `${COLLAPSED_HEIGHT_PX}px` }}
						>
							{/* Attachment button */}
							<Tooltip>
								<TooltipTrigger asChild>
									<Button
										type="button"
										variant="ghost"
										size="icon-lg"
										onClick={() => fileInputRef.current?.click()}
										disabled={attachDisabled}
										className="shrink-0 rounded-full"
									>
										<Paperclip className="size-4" />
									</Button>
								</TooltipTrigger>
								<TooltipContent>
									{attachDisabled && allowedInputTypes?.length === 0
										? t('textInput.attachNotSupported')
										: t('textInput.attach')}
								</TooltipContent>
							</Tooltip>

							{/* Send / Stop button — driven by ``sendButton`` config */}
							<Tooltip>
								<TooltipTrigger asChild>
									<Button
										type="button"
										onClick={sendButton.onClick}
										disabled={sendButton.disabled}
										size="icon-lg"
										className="shrink-0 rounded-full"
									>
										<sendButton.icon className="h-4 w-4" />
									</Button>
								</TooltipTrigger>
								<TooltipContent>{sendButton.tooltip}</TooltipContent>
							</Tooltip>

							{/* Hidden file input */}
							<input
								ref={fileInputRef}
								type="file"
								multiple
								accept={acceptAttr}
								onChange={handleFileSelect}
								className="hidden"
							/>
						</div>
					</div>
				</div>
			</div>
		);
	},
);

TextInput.displayName = 'TextInput';
