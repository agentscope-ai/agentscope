import { ChevronLeft, ChevronRight, Download, FileText, Loader2 } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';

import { knowledgeBaseApi } from '@/api';
import type { KnowledgeChunk, KnowledgeDocumentView } from '@/api';
import { ApiError } from '@/api/client';
import { Markdown } from '@/components/markdown';
import { Badge } from '@/components/ui/badge.tsx';
import { Button } from '@/components/ui/button.tsx';
import {
	Empty,
	EmptyDescription,
	EmptyHeader,
	EmptyMedia,
	EmptyTitle,
} from '@/components/ui/empty.tsx';
import {
	Sheet,
	SheetContent,
	SheetDescription,
	SheetFooter,
	SheetHeader,
	SheetTitle,
} from '@/components/ui/sheet.tsx';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs.tsx';
import { useTranslation } from '@/i18n/useI18n.ts';

const CHUNK_PAGE_SIZE = 20;

/**
 * Image types the backend serves with `Content-Disposition: inline`.
 * Anything else — notably `image/svg+xml`, which can carry script —
 * comes back as an attachment, so an `<img>` preview would break;
 * those types fall through to the download-button branch instead.
 */
const INLINE_IMAGE_TYPES = ['image/png', 'image/jpeg', 'image/gif', 'image/webp', 'image/bmp'];

/**
 * Largest text file rendered inline. Text previews pull the whole body
 * into the tab, so a multi-hundred-megabyte upload would freeze it —
 * past this the drawer offers a download instead.
 */
const MAX_INLINE_TEXT_BYTES = 2 * 1024 * 1024;

interface Props {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	knowledgeBaseId: string;
	document: KnowledgeDocumentView;
}

/** Media type with parameters stripped, lowercased — `''` when unknown. */
function mediaType(doc: KnowledgeDocumentView): string {
	return (doc.content_type ?? '').split(';')[0].trim().toLowerCase();
}

/**
 * Right-side drawer showing a ready document's indexed chunks (paged,
 * in `chunk_index` order) and a preview of the original uploaded file.
 */
export function DocumentDetailDrawer({ open, onOpenChange, knowledgeBaseId, document }: Props) {
	const { t } = useTranslation();

	return (
		<Sheet open={open} onOpenChange={onOpenChange}>
			<SheetContent className="flex w-full sm:!max-w-[560px] flex-col gap-y-4 p-4">
				<SheetHeader className="px-0">
					<SheetTitle className="flex items-center gap-x-2">
						<FileText className="size-4 shrink-0" />
						<span className="truncate">{document.filename}</span>
					</SheetTitle>
					<SheetDescription>
						{t('knowledge.documentDetail.description', {
							count: document.chunk_count,
						})}
					</SheetDescription>
				</SheetHeader>

				<Tabs defaultValue="chunks" className="flex min-h-0 flex-1 flex-col gap-y-3">
					<TabsList className="w-fit">
						<TabsTrigger value="chunks">
							{t('knowledge.documentDetail.chunksTab')}
						</TabsTrigger>
						<TabsTrigger value="preview">
							{t('knowledge.documentDetail.previewTab')}
						</TabsTrigger>
					</TabsList>
					<TabsContent value="chunks" className="min-h-0 flex-1 overflow-y-auto">
						<ChunksTab
							open={open}
							knowledgeBaseId={knowledgeBaseId}
							document={document}
						/>
					</TabsContent>
					<TabsContent value="preview" className="min-h-0 flex-1 overflow-y-auto">
						<PreviewTab
							open={open}
							knowledgeBaseId={knowledgeBaseId}
							document={document}
						/>
					</TabsContent>
				</Tabs>

				<SheetFooter className="px-0">
					<Button variant="ghost" onClick={() => onOpenChange(false)}>
						{t('common.close')}
					</Button>
				</SheetFooter>
			</SheetContent>
		</Sheet>
	);
}

interface TabProps {
	open: boolean;
	knowledgeBaseId: string;
	document: KnowledgeDocumentView;
}

function ChunksTab({ open, knowledgeBaseId, document }: TabProps) {
	const { t } = useTranslation();
	const [page, setPage] = useState(1);
	const [chunks, setChunks] = useState<KnowledgeChunk[]>([]);
	const [total, setTotal] = useState(document.chunk_count);
	const [loading, setLoading] = useState(false);
	const [unsupported, setUnsupported] = useState(false);
	const [error, setError] = useState<string | null>(null);

	useEffect(() => {
		if (!open) return;
		let cancelled = false;
		setLoading(true);
		setError(null);
		knowledgeBaseApi
			.listDocumentChunks(knowledgeBaseId, document.id, page, CHUNK_PAGE_SIZE)
			.then((res) => {
				if (cancelled) return;
				setChunks(res.chunks);
				setTotal(res.total);
			})
			.catch((e) => {
				if (cancelled) return;
				// 501 — the configured vector store cannot list chunks.
				if (e instanceof ApiError && e.status === 501) setUnsupported(true);
				else setError((e as Error).message || String(e));
			})
			.finally(() => {
				if (!cancelled) setLoading(false);
			});
		return () => {
			cancelled = true;
		};
	}, [open, knowledgeBaseId, document.id, page]);

	if (unsupported) {
		return (
			<Empty className="border-none py-6">
				<EmptyHeader>
					<EmptyMedia variant="icon">
						<FileText />
					</EmptyMedia>
					<EmptyTitle>{t('knowledge.documentDetail.chunksUnsupportedTitle')}</EmptyTitle>
					<EmptyDescription>
						{t('knowledge.documentDetail.chunksUnsupportedDescription')}
					</EmptyDescription>
				</EmptyHeader>
			</Empty>
		);
	}

	const pageCount = Math.max(1, Math.ceil(total / CHUNK_PAGE_SIZE));

	return (
		<div className="flex flex-col gap-y-3">
			{error && <p className="text-destructive text-sm">{error}</p>}
			{loading ? (
				<div className="flex justify-center py-8">
					<Loader2 className="text-muted-foreground size-4 animate-spin" />
				</div>
			) : chunks.length === 0 ? (
				<Empty className="border-none py-6">
					<EmptyHeader>
						<EmptyMedia variant="icon">
							<FileText />
						</EmptyMedia>
						<EmptyTitle>{t('knowledge.documentDetail.chunksEmptyTitle')}</EmptyTitle>
						<EmptyDescription>
							{t('knowledge.documentDetail.chunksEmptyDescription')}
						</EmptyDescription>
					</EmptyHeader>
				</Empty>
			) : (
				chunks.map((chunk) => (
					<div
						key={chunk.chunk_index}
						className="bg-card flex flex-col gap-y-2 rounded-md border p-3"
					>
						<div className="text-muted-foreground flex items-center gap-x-2 text-xs">
							<Badge variant="secondary" className="font-mono">
								{t('knowledge.test.chunkPosition', {
									index: chunk.chunk_index + 1,
									total: chunk.total_chunks,
								})}
							</Badge>
						</div>
						<p className="text-sm break-words whitespace-pre-wrap">
							{chunk.content &&
							typeof chunk.content === 'object' &&
							'text' in chunk.content
								? String(chunk.content.text ?? '')
								: JSON.stringify(chunk.content)}
						</p>
					</div>
				))
			)}
			{total > CHUNK_PAGE_SIZE && (
				<div className="flex items-center justify-between pt-1">
					<Button
						variant="outline"
						size="sm"
						disabled={loading || page <= 1}
						onClick={() => setPage((p) => Math.max(1, p - 1))}
					>
						<ChevronLeft className="size-3.5" />
						{t('knowledge.documentDetail.previousPage')}
					</Button>
					<span className="text-muted-foreground text-xs">
						{t('knowledge.documentDetail.pageIndicator', {
							page,
							pageCount,
						})}
					</span>
					<Button
						variant="outline"
						size="sm"
						disabled={loading || page >= pageCount}
						onClick={() => setPage((p) => Math.min(pageCount, p + 1))}
					>
						{t('knowledge.documentDetail.nextPage')}
						<ChevronRight className="size-3.5" />
					</Button>
				</div>
			)}
		</div>
	);
}

function PreviewTab({ open, knowledgeBaseId, document }: TabProps) {
	const { t } = useTranslation();
	const media = mediaType(document);
	// Oversized text falls through to the download branch rather than
	// being streamed into the tab.
	const isTextType = media === 'text/markdown' || media === 'text/plain';
	const tooLargeToInline = isTextType && document.size > MAX_INLINE_TEXT_BYTES;
	const isText = isTextType && !tooLargeToInline;
	const isPdf = media === 'application/pdf';
	const isImage = INLINE_IMAGE_TYPES.includes(media);

	const [text, setText] = useState<string | null>(null);
	const [tokenUrl, setTokenUrl] = useState<string | null>(null);
	const [loading, setLoading] = useState(false);
	const [error, setError] = useState<string | null>(null);

	useEffect(() => {
		if (!open) return;
		let cancelled = false;
		setLoading(true);
		setError(null);
		const load = async () => {
			if (isText) {
				const body = await knowledgeBaseApi.fetchDocumentText(knowledgeBaseId, document.id);
				if (!cancelled) setText(body);
			} else if (isPdf || isImage) {
				const { token } = await knowledgeBaseApi.createDocumentDownloadToken(
					knowledgeBaseId,
					document.id,
				);
				if (!cancelled) {
					setTokenUrl(
						knowledgeBaseApi.documentContentUrl(knowledgeBaseId, document.id, token),
					);
				}
			}
		};
		load()
			.catch((e) => {
				if (!cancelled) setError((e as Error).message || String(e));
			})
			.finally(() => {
				if (!cancelled) setLoading(false);
			});
		return () => {
			cancelled = true;
		};
	}, [open, knowledgeBaseId, document.id, isText, isPdf, isImage]);

	const handleDownload = useCallback(async () => {
		try {
			const { token } = await knowledgeBaseApi.createDocumentDownloadToken(
				knowledgeBaseId,
				document.id,
			);
			window.open(
				knowledgeBaseApi.documentContentUrl(knowledgeBaseId, document.id, token, true),
				'_blank',
			);
		} catch {
			// The client already toasts the failure.
		}
	}, [knowledgeBaseId, document.id]);

	if (loading) {
		return (
			<div className="flex justify-center py-8">
				<Loader2 className="text-muted-foreground size-4 animate-spin" />
			</div>
		);
	}
	if (error) {
		return <p className="text-destructive text-sm">{error}</p>;
	}
	if (isText && text !== null) {
		return media === 'text/markdown' ? (
			<Markdown>{text}</Markdown>
		) : (
			<pre className="text-sm break-words whitespace-pre-wrap">{text}</pre>
		);
	}
	if (isPdf && tokenUrl) {
		return <iframe src={tokenUrl} title={document.filename} className="h-full w-full" />;
	}
	if (isImage && tokenUrl) {
		return <img src={tokenUrl} alt={document.filename} className="max-w-full" />;
	}
	return (
		<Empty className="border-none py-6">
			<EmptyHeader>
				<EmptyMedia variant="icon">
					<Download />
				</EmptyMedia>
				<EmptyTitle>
					{t(
						tooLargeToInline
							? 'knowledge.documentDetail.previewTooLargeTitle'
							: 'knowledge.documentDetail.previewUnavailableTitle',
					)}
				</EmptyTitle>
				<EmptyDescription>
					{t(
						tooLargeToInline
							? 'knowledge.documentDetail.previewTooLargeDescription'
							: 'knowledge.documentDetail.previewUnavailableDescription',
					)}
				</EmptyDescription>
			</EmptyHeader>
			<Button size="sm" variant="outline" onClick={handleDownload}>
				<Download className="size-3.5" />
				{t('knowledge.documentDetail.downloadButton')}
			</Button>
		</Empty>
	);
}
