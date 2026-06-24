import type { ContentBlock, TextBlock } from '@agentscope-ai/agentscope/message';
import { Paperclip, Send, Loader2, X } from 'lucide-react';
import React, {
	useState,
	useRef,
	useMemo,
	type KeyboardEvent,
	useImperativeHandle,
	forwardRef,
} from 'react';

import { Button } from '../ui/button';
import { Kbd } from '../ui/kbd';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { useTranslation } from '@/i18n/useI18n.ts';
import { cn } from '@/lib/utils';
import { isMac } from '@/utils/platform';

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
	/** When true, allow sending attached files without text (for realtime agents). */
	allowFilesOnly?: boolean;
}

export interface TextInputRef {
	focus: () => void;
}

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
			allowFilesOnly = false,
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

		const readyFiles = files.filter((f) => f.status === 'done' && f.block);
		const hasContent = !!value.trim() || (allowFilesOnly && readyFiles.length > 0);

		const handleSend = () => {
			if (!hasContent || disabled || hasProcessing) return;

			const blocks: ContentBlock[] = [];

			if (value.trim()) {
				const textBlock: TextBlock = {
					id: crypto.randomUUID(),
					type: 'text',
					text: value.trim(),
				};
				blocks.push(textBlock);
			}

			files.forEach((f) => {
				if (f.status === 'done' && f.block) {
					blocks.push(f.block);
				}
			});

			onSend?.(blocks);
			setValue('');
			setFiles([]);
		};

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
			<div
				id="tour-chat-input"
				className={cn(
					'flex flex-col gap-2 rounded-2xl border bg-background p-3',
					className,
				)}
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
									onClick={() => setFiles(files.filter((_, i) => i !== index))}
									className="text-muted-foreground hover:text-foreground"
								>
									<X className="h-3 w-3" />
								</button>
							</div>
						))}
					</div>
				)}

				{/* Input area */}
				<div className="relative">
					<div className="relative">
						{/* Hidden measurement element */}
						<span
							ref={measureRef}
							className="invisible absolute whitespace-pre text-sm"
							style={{
								font: 'inherit',
								padding: '0.5rem 0.75rem',
								lineHeight: '1.5em',
							}}
						>
							{value}
						</span>

						<textarea
							ref={textareaRef}
							value={value}
							onChange={(e) => setValue(e.target.value)}
							onKeyDown={handleKeyDown}
							onFocus={() => setIsFocused(true)}
							onBlur={() => setIsFocused(false)}
							placeholder={defaultPlaceholder}
							disabled={disabled}
							rows={3}
							className="w-full resize-none rounded-md border-0 bg-transparent px-3 py-2 text-sm outline-none placeholder:text-muted-foreground focus:outline-none disabled:cursor-not-allowed disabled:opacity-50"
							style={{
								maxHeight: 'calc(1.5em * 6)',
								lineHeight: '1.5em',
								overflowY: 'auto',
							}}
							autoFocus={true}
						/>

						{/* Autocomplete suggestion - using absolute positioning overlay */}
						{suggestion && isFocused && (
							<div
								className="pointer-events-none absolute left-0 top-0 px-3 py-2 text-sm"
								style={{
									lineHeight: '1.5em',
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

					{/* Button row */}
					<div className="mt-2 flex items-center justify-between">
						<div>
							<span
								className={`text-muted-foreground text-sm ${!isFocused && 'hidden'}`}
							>
								<Kbd>{isMac() ? '⇧' : 'Shift'}</Kbd> + <Kbd>Enter</Kbd>{' '}
								{t('textInput.newLine')}
							</span>
						</div>
						<div className="flex gap-2">
							{/* Attachment button */}
							<Tooltip>
								<TooltipTrigger asChild>
									<Button
										type="button"
										variant="ghost"
										size="icon"
										onClick={() => fileInputRef.current?.click()}
										disabled={attachDisabled}
										className="shrink-0 rounded-full"
									>
										<Paperclip className="h-4 w-4" />
									</Button>
								</TooltipTrigger>
								<TooltipContent>
									{attachDisabled && allowedInputTypes?.length === 0
										? t('textInput.attachNotSupported')
										: t('textInput.attach')}
								</TooltipContent>
							</Tooltip>

							{/* Send button */}
							<Tooltip>
								<TooltipTrigger asChild>
									<Button
										type="button"
										onClick={handleSend}
										disabled={disabled || !hasContent || hasProcessing}
										size="icon"
										className="shrink-0 rounded-full"
									>
										<Send className="h-4 w-4" />
									</Button>
								</TooltipTrigger>
								<TooltipContent>{t('textInput.send')}</TooltipContent>
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
