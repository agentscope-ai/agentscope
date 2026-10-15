import type { DataBlock } from '@agentscope-ai/agentscope/message';
import { Download, Eye, EyeOff, FileText, Loader2 } from 'lucide-react';
import { useEffect, useState } from 'react';

import { workspaceApi } from '@/api';
import { Alert, AlertDescription } from '@/components/ui/alert';
import {
	Attachment,
	AttachmentContent,
	AttachmentDescription,
	AttachmentMedia,
	AttachmentTitle,
} from '@/components/ui/attachment';
import { Button } from '@/components/ui/button';
import { useTranslation } from '@/i18n/useI18n';
import { formatApiErrorForAlert } from '@/lib/api-error';

interface OfficeFilePreviewProps {
	agentId: string;
	sessionId: string;
	name: string;
	mediaType: string;
	loadOriginal: () => Promise<File>;
	getDownloadUrl: () => Promise<string>;
	showDownload?: boolean;
	downloadFromSource?: boolean;
}

export function isOfficeDocument(name: string, mediaType: string): boolean {
	const extension = name.split('.').pop()?.toLowerCase();
	return (
		extension === 'docx' ||
		extension === 'pptx' ||
		mediaType === 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' ||
		mediaType === 'application/vnd.openxmlformats-officedocument.presentationml.presentation'
	);
}

export function triggerFileDownload(href: string, name: string) {
	const link = document.createElement('a');
	link.href = href;
	link.download = name;
	link.rel = 'noopener noreferrer';
	document.body.appendChild(link);
	link.click();
	link.remove();
}

export function dataBlockUrl(block: DataBlock): string {
	return block.source.type === 'url'
		? block.source.url
		: `data:${block.source.media_type};base64,${block.source.data}`;
}

function workspaceDataBlockPath(block: DataBlock): string | null {
	if (block.source.type !== 'url' || !block.source.url.startsWith('workspace:')) {
		return null;
	}
	const prefix = 'workspace:///';
	const reference = block.source.url;
	if (!reference.startsWith(prefix) || reference.includes('?') || reference.includes('#')) {
		throw new Error('Invalid workspace file reference.');
	}
	const path = decodeURIComponent(reference.slice(prefix.length));
	if (
		!path ||
		path.startsWith('/') ||
		path.includes('\\') ||
		path.split('/').some((part) => !part || part === '.' || part === '..')
	) {
		throw new Error('Invalid workspace file reference.');
	}
	return path;
}

export function dataBlockName(block: DataBlock): string {
	if (block.name) return block.name;
	if (block.source.media_type.includes('presentationml.presentation')) {
		return 'document.pptx';
	}
	if (block.source.media_type.includes('wordprocessingml.document')) {
		return 'document.docx';
	}
	return '';
}

export async function fileFromDataBlock(
	block: DataBlock,
	name: string,
	agentId: string,
	sessionId: string,
): Promise<File> {
	const path = workspaceDataBlockPath(block);
	const blob = path
		? await workspaceApi.readFile(agentId, sessionId, path)
		: await fetch(dataBlockUrl(block)).then(async (response) => {
				if (!response.ok) {
					throw new Error(response.statusText || `HTTP ${response.status}`);
				}
				return response.blob();
			});
	return new globalThis.File([blob], name, {
		type: blob.type || block.source.media_type,
	});
}

export async function dataBlockDownloadUrl(
	block: DataBlock,
	agentId: string,
	sessionId: string,
): Promise<string> {
	const path = workspaceDataBlockPath(block);
	if (!path) return dataBlockUrl(block);
	const { token } = await workspaceApi.downloadToken(agentId, sessionId, path);
	return workspaceApi.downloadUrl(agentId, sessionId, path, token);
}

export function OfficeFilePreview({
	agentId,
	sessionId,
	name,
	mediaType,
	loadOriginal,
	getDownloadUrl,
	showDownload = true,
	downloadFromSource = false,
}: OfficeFilePreviewProps) {
	const { t } = useTranslation();
	const [pdfUrl, setPdfUrl] = useState<string | null>(null);
	const [loading, setLoading] = useState(false);
	const [downloading, setDownloading] = useState(false);
	const [error, setError] = useState<string | null>(null);

	useEffect(
		() => () => {
			if (pdfUrl) URL.revokeObjectURL(pdfUrl);
		},
		[pdfUrl],
	);

	const handlePreview = async () => {
		if (pdfUrl) {
			setPdfUrl(null);
			return;
		}
		setLoading(true);
		setError(null);
		try {
			const file = await loadOriginal();
			const pdf = await workspaceApi.officePreview(agentId, sessionId, file);
			setPdfUrl(URL.createObjectURL(pdf));
		} catch (e) {
			setError(formatApiErrorForAlert(e));
		} finally {
			setLoading(false);
		}
	};

	const handleDownload = async () => {
		setDownloading(true);
		setError(null);
		try {
			if (downloadFromSource) {
				try {
					const file = await loadOriginal();
					const sourceUrl = URL.createObjectURL(file);
					triggerFileDownload(sourceUrl, name);
					window.setTimeout(() => URL.revokeObjectURL(sourceUrl), 1000);
					return;
				} catch (error) {
					if (!(error instanceof TypeError)) throw error;
					// A remote URL may allow navigation but disallow fetching for a blob download.
					triggerFileDownload(await getDownloadUrl(), name);
					return;
				}
			}
			triggerFileDownload(await getDownloadUrl(), name);
		} catch (e) {
			setError(formatApiErrorForAlert(e));
		} finally {
			setDownloading(false);
		}
	};

	const extension = name.split('.').pop()?.toUpperCase() || mediaType;

	return (
		<div className="w-full min-w-0 space-y-2">
			<Attachment className="w-full">
				<AttachmentMedia>
					<FileText />
				</AttachmentMedia>
				<AttachmentContent>
					<AttachmentTitle>{name}</AttachmentTitle>
					<AttachmentDescription>{extension}</AttachmentDescription>
				</AttachmentContent>
				<div className="flex shrink-0 items-center gap-1 pr-1">
					<Button
						size="sm"
						variant="outline"
						onClick={() => void handlePreview()}
						disabled={loading}
					>
						{loading ? (
							<Loader2 className="animate-spin" />
						) : pdfUrl ? (
							<EyeOff />
						) : (
							<Eye />
						)}
						{pdfUrl ? t('workspaceFiles.closePreview') : t('workspaceFiles.preview')}
					</Button>
					{showDownload && (
						<Button
							size="sm"
							variant="ghost"
							onClick={() => void handleDownload()}
							disabled={downloading}
						>
							{downloading ? <Loader2 className="animate-spin" /> : <Download />}
							{t('workspaceFiles.download')}
						</Button>
					)}
				</div>
			</Attachment>
			{error && (
				<Alert variant="destructive">
					<AlertDescription className="whitespace-pre-wrap">{error}</AlertDescription>
				</Alert>
			)}
			{pdfUrl && (
				<iframe
					title={t('workspaceFiles.previewTitle', { name })}
					src={pdfUrl}
					className="h-[min(65vh,44rem)] w-full rounded-md border bg-background"
				/>
			)}
		</div>
	);
}
