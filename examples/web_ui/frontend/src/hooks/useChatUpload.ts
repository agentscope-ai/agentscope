import { useCallback, useRef, useState } from 'react';
import { uploadsApi, type ChatFileRef, type UploadedFile } from '@/api/uploads';

/**
 * Manages files uploaded to the BocomADP upload endpoint for the current
 * session. Files are uploaded up-front (via `uploadsApi.upload`) and surfaced
 * to the chat send path as `ChatFileRef`s; the backend's `UploadsMiddleware`
 * then injects their converted outlines into the human message.
 *
 * Call `queueUpload` from the chat `fileProcessor`: for server-processed file
 * types (convertible docs and images) it uploads and stores a virtual-path ref;
 * for other types it returns `null` so the caller falls back to inline blocks.
 */
export function useChatUpload(
	agentId: string | null | undefined,
	sessionId: string | null | undefined,
) {
	const [uploaded, setUploaded] = useState<UploadedFile[]>([]);
	const [uploading, setUploading] = useState(false);
	const [error, setError] = useState<string | null>(null);
	// Keep the latest refs so the send closure always sees fresh data.
	const refsRef = useRef<ChatFileRef[]>([]);

	const queueUpload = useCallback(
		async (file: File): Promise<ChatFileRef | null> => {
			if (!agentId || !sessionId) return null;
			setUploading(true);
			setError(null);
			try {
				const meta = await uploadsApi.upload(agentId, sessionId, file);
				const ref: ChatFileRef = {
					filename: meta.filename,
					filetype: file.type || 'application/octet-stream',
					virtual_path: meta.virtual_path,
				};
				setUploaded((prev) => {
					const next = [...prev.filter((p) => p.filename !== meta.filename), meta];
					refsRef.current = next.map((m) => ({
						filename: m.filename,
						filetype: file.type || 'application/octet-stream',
						virtual_path: m.virtual_path,
					}));
					return next;
				});
				return ref;
			} catch (e) {
				const msg = e instanceof Error ? e.message : String(e);
				setError(msg);
				return null;
			} finally {
				setUploading(false);
			}
		},
		[agentId, sessionId],
	);

	/** Consume and clear the queued refs (called right before a chat send). */
	const takeRefs = useCallback((): ChatFileRef[] => {
		const refs = refsRef.current;
		refsRef.current = [];
		setUploaded([]);
		return refs;
	}, []);

	const remove = useCallback(
		async (filename: string) => {
			if (!agentId || !sessionId) return;
			try {
				await uploadsApi.delete(agentId, sessionId, filename);
			} catch {
				/* best-effort */
			}
			setUploaded((prev) => {
				const next = prev.filter((p) => p.filename !== filename);
				refsRef.current = next.map((m) => ({
					filename: m.filename,
					filetype: '',
					virtual_path: m.virtual_path,
				}));
				return next;
			});
		},
		[agentId, sessionId],
	);

	return { uploaded, uploading, error, queueUpload, takeRefs, remove };
}
