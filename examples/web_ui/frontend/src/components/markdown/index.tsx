import { cjk } from '@streamdown/cjk';
import { code } from '@streamdown/code';
import { math } from '@streamdown/math';
import { mermaid } from '@streamdown/mermaid';
import { WrapText } from 'lucide-react';
import * as React from 'react';
import {
	CodeBlock,
	CodeBlockCopyButton,
	CodeBlockDownloadButton,
	type CustomRendererProps,
	Streamdown,
} from 'streamdown';

import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { useTranslation } from '@/i18n/useI18n';
import { cn } from '@/lib/utils';

const DEFAULT_PLUGINS = { code, math, cjk, mermaid };
const CHAT_PLUGINS = {
	...DEFAULT_PLUGINS,
	renderers: [{ language: ['markdown', 'md'], component: MarkdownFence }],
};

function ActionTooltip({ label, children }: { label: string; children: React.ReactElement }) {
	return (
		<Tooltip disableHoverableContent>
			<TooltipTrigger asChild>
				<span className="inline-flex [&>button:disabled]:pointer-events-none">
					{children}
				</span>
			</TooltipTrigger>
			<TooltipContent sideOffset={4}>{label}</TooltipContent>
		</Tooltip>
	);
}

function MarkdownFence({ code, language, isIncomplete, meta }: CustomRendererProps) {
	const { t } = useTranslation();
	const [wrap, setWrap] = React.useState(false);
	const wrapHint = wrap ? t('messageBubble.disableWrap') : t('messageBubble.enableWrap');
	const downloadHint = t('messageBubble.downloadMarkdown');
	const copyHint = t('messageBubble.copyMarkdown');
	const startLine = meta?.match(/startLine=(\d+)/)?.[1];

	return (
		<CodeBlock
			code={code}
			language={language}
			isIncomplete={isIncomplete}
			startLine={Number(startLine) || undefined}
			lineNumbers={!/\bnoLineNumbers\b/.test(meta ?? '')}
			className={wrap ? 'whitespace-pre-wrap wrap-anywhere' : undefined}
		>
			<ActionTooltip label={wrapHint}>
				<button
					type="button"
					aria-label={t('messageBubble.wrapLines')}
					aria-pressed={wrap}
					onClick={() => setWrap((current) => !current)}
					className="cursor-pointer rounded p-1 text-muted-foreground hover:text-foreground aria-pressed:bg-muted aria-pressed:text-foreground"
				>
					<WrapText size={14} />
				</button>
			</ActionTooltip>
			<ActionTooltip label={downloadHint}>
				<CodeBlockDownloadButton
					language={language}
					aria-label={downloadHint}
					title={undefined}
				/>
			</ActionTooltip>
			<ActionTooltip label={copyHint}>
				<CodeBlockCopyButton aria-label={copyHint} title={undefined} />
			</ActionTooltip>
		</CodeBlock>
	);
}

function Markdown({
	className,
	wrapControl = false,
	plugins = wrapControl ? CHAT_PLUGINS : DEFAULT_PLUGINS,
	controls = false,
	...props
}: React.ComponentProps<typeof Streamdown> & { wrapControl?: boolean }) {
	return (
		<Streamdown
			data-slot="markdown"
			plugins={plugins}
			controls={controls}
			className={cn('cn-markdown w-full min-w-0 overflow-hidden', className)}
			{...props}
		/>
	);
}

export { Markdown };
