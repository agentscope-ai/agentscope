import { Download, File, FileAudio, FileImage, FileText, FileVideo } from 'lucide-react';
import * as mime from 'mime-types';

interface FileIconProps {
	/** The MIME type, e.g. `application/pdf`. */
	mediaType: string;
	/** Optional CSS classes forwarded to the icon. */
	className?: string;
}

/**
 * Render a representative icon for a file based on its MIME type.
 *
 * @param root0 - The component props.
 * @param root0.mediaType - The MIME type of the file.
 * @param root0.className - CSS classes forwarded to the icon.
 * @returns A lucide icon element appropriate for the file kind.
 */
function FileIcon({ mediaType, className }: FileIconProps) {
	switch (mediaType.split('/')[0]) {
		case 'image':
			return <FileImage className={className} />;
		case 'audio':
			return <FileAudio className={className} />;
		case 'video':
			return <FileVideo className={className} />;
		case 'text':
			return <FileText className={className} />;
		default:
			return mediaType === 'application/pdf' ? (
				<FileText className={className} />
			) : (
				<File className={className} />
			);
	}
}

/**
 * Classify a file href for safe, correct linking.
 *
 * - `safe`: only `http`/`https`/`data`/`blob` schemes become a clickable
 *   link, so a malicious model-supplied URL (e.g. `javascript:`) can't turn
 *   the card into a click-to-execute vector.
 * - `downloadable`: the HTML `download` attribute is only honoured for
 *   same-origin, `blob:`, and `data:` URLs; cross-origin remote URLs ignore
 *   it and just open. We only set `download` when it will actually work, and
 *   open remote URLs in a new tab instead.
 *
 * @param href - The candidate href (remote URL or `data:` URL).
 * @returns Whether the href is safe to link and whether `download` applies.
 */
function classifyHref(href: string): { safe: boolean; downloadable: boolean } {
	let url: URL;
	try {
		url = new URL(href, window.location.origin);
	} catch {
		return { safe: false, downloadable: false };
	}
	const safe = ['http:', 'https:', 'data:', 'blob:'].includes(url.protocol);
	const downloadable =
		url.protocol === 'data:' ||
		url.protocol === 'blob:' ||
		url.origin === window.location.origin;
	return { safe, downloadable };
}

interface FileAttachmentProps {
	/** Display name of the file (falls back to a name generated from the extension). */
	name?: string;
	/** Resolved href — a remote URL or a `data:` URL — used for download. */
	href: string;
	/** MIME type of the file, e.g. `application/pdf`. */
	mediaType: string;
}

/**
 * A compact, downloadable card for a non-previewable file attachment
 * (PDF, Word, Excel, plain-text blobs, …).
 *
 * Image / audio / video data blocks are rendered inline by the caller;
 * every other MIME type falls back to this card instead of rendering
 * nothing at all.
 *
 * @param root0 - The component props.
 * @param root0.name - Display name of the file.
 * @param root0.href - Download href (remote URL or `data:` URL).
 * @param root0.mediaType - MIME type of the file.
 * @returns A downloadable file-attachment card.
 */
export function FileAttachment({ name, href, mediaType }: FileAttachmentProps) {
	const ext = (mime.extension(mediaType) || '').toUpperCase();
	const displayName = name || (ext ? `file.${ext.toLowerCase()}` : 'file');
	const { safe, downloadable } = classifyHref(href);

	// Only safe schemes become a real link. Downloadable hrefs (data:/blob:/
	// same-origin) get `download`; remote URLs open in a new tab, where the
	// `download` attribute would be ignored anyway.
	const linkProps = !safe
		? {}
		: downloadable
			? { href, download: displayName }
			: { href, target: '_blank' as const, rel: 'noopener noreferrer' };

	return (
		<a
			{...linkProps}
			className="group flex w-fit max-w-xs items-center gap-3 rounded-lg border bg-muted/40 px-3 py-2 no-underline transition-colors hover:bg-muted"
		>
			<span className="flex size-9 shrink-0 items-center justify-center rounded-md bg-background text-muted-foreground">
				<FileIcon mediaType={mediaType} className="size-4" />
			</span>
			<span className="flex min-w-0 flex-col">
				<span className="truncate text-sm font-medium text-foreground">{displayName}</span>
				{ext && <span className="text-xs text-muted-foreground">{ext}</span>}
			</span>
			<Download className="size-4 shrink-0 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100" />
		</a>
	);
}
