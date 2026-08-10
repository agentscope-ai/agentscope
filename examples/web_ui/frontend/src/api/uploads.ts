import { buildApiUrl, getUserId, ApiError } from './client';

//
// API client for the BocomADP file-upload capability
// (backend: bocomadp/routers/uploads.py, prefix /api/uploads).
//
// Two flows:
//   1. sync upload   POST /api/uploads/files            (file <= streaming threshold)
//   2. stream upload POST /api/uploads/files/streaming  (file > threshold, chunked)
// Plus listing / delete / download / limits.
//

export interface UploadLimits {
	max_file_size_mb: number;
	max_files_per_session: number;
	streaming_threshold_mb: number;
}

export interface UploadedFile {
	filename: string;
	virtual_path: string;
	converted: boolean;
	artifact_url?: string | null;
}

export interface ChatFileRef {
	filename: string;
	filetype: string;
	virtual_path: string;
}

/** File kinds the backend converts to Markdown and injects as an outline. */
const SERVER_PROCESSED_EXT = new Set([
	'.txt',
	'.md',
	'.markdown',
	'.csv',
	'.tsv',
	'.json',
	'.jsonl',
	'.xml',
	'.log',
	'.yaml',
	'.yml',
	'.toml',
	'.ini',
	'.cfg',
	'.conf',
	'.py',
	'.js',
	'.jsx',
	'.ts',
	'.tsx',
	'.java',
	'.go',
	'.c',
	'.cpp',
	'.h',
	'.cs',
	'.rb',
	'.php',
	'.rs',
	'.sql',
	'.sh',
	'.pdf',
	'.doc',
	'.docx',
	'.ppt',
	'.pptx',
	'.xls',
	'.xlsx',
	'.xlsm',
	'.html',
	'.htm',
]);

/** Image types keep the existing inline-attachment behaviour (not server-uploaded). */
export function isServerProcessedFile(file: File): boolean {
	if (file.type.startsWith('image/')) return false;
	const ext = '.' + (file.name.split('.').pop() ?? '').toLowerCase();
	return SERVER_PROCESSED_EXT.has(ext);
}

async function parseError(res: Response): Promise<ApiError> {
	const text = await res.text();
	try {
		const json = JSON.parse(text) as { detail?: unknown };
		if (typeof json.detail === 'string') return new ApiError(res.status, json.detail);
		if (json.detail !== undefined) return new ApiError(res.status, JSON.stringify(json.detail));
	} catch {
		/* fall through */
	}
	return new ApiError(res.status, text || res.statusText);
}

export const uploadsApi = {
	async limits(): Promise<UploadLimits> {
		const res = await fetch(buildApiUrl('/uploads/limits'));
		if (!res.ok) throw await parseError(res);
		return (await res.json()) as UploadLimits;
	},

	async list(sessionId: string): Promise<UploadedFile[]> {
		const url = buildApiUrl('/uploads/files');
		url.searchParams.set('user_id', getUserId());
		url.searchParams.set('session_id', sessionId);
		const res = await fetch(url);
		if (!res.ok) throw await parseError(res);
		return (await res.json()) as UploadedFile[];
	},

	async delete(sessionId: string, filename: string): Promise<void> {
		const url = buildApiUrl('/uploads/files');
		url.searchParams.set('user_id', getUserId());
		url.searchParams.set('session_id', sessionId);
		url.searchParams.set('filename', filename);
		const res = await fetch(url, { method: 'DELETE' });
		if (!res.ok) throw await parseError(res);
	},

	/** Upload a file to the server, returning its virtual path ref. */
	async upload(
		sessionId: string,
		file: File,
		onProgress?: (loaded: number, total: number) => void,
	): Promise<UploadedFile> {
		const lim = await this.limits().catch(() => null);
		const threshold = (lim?.streaming_threshold_mb ?? 10) * 1024 * 1024;

		if (file.size > threshold) {
			return this.uploadStreaming(sessionId, file, threshold, onProgress);
		}
		return this.uploadSync(sessionId, file, onProgress);
	},

	async uploadSync(
		sessionId: string,
		file: File,
		onProgress?: (loaded: number, total: number) => void,
	): Promise<UploadedFile> {
		const fd = new FormData();
		fd.append('file', file);
		fd.append('user_id', getUserId());
		fd.append('session_id', sessionId);

		const res = await fetch(buildApiUrl('/uploads/files'), {
			method: 'POST',
			body: fd,
		});
		if (!res.ok) throw await parseError(res);
		onProgress?.(file.size, file.size);
		return (await res.json()) as UploadedFile;
	},

	async uploadStreaming(
		sessionId: string,
		file: File,
		threshold: number,
		onProgress?: (loaded: number, total: number) => void,
	): Promise<UploadedFile> {
		const CHUNK = 5 * 1024 * 1024;
		const total = Math.ceil(file.size / CHUNK);
		let loaded = 0;
		let last: UploadedFile | null = null;

		for (let i = 0; i < total; i++) {
			const blob = file.slice(i * CHUNK, (i + 1) * CHUNK);
			const fd = new FormData();
			fd.append('file', new Blob([blob]), file.name);
			fd.append('user_id', getUserId());
			fd.append('session_id', sessionId);

			const res = await fetch(buildApiUrl('/uploads/files/streaming'), {
				method: 'POST',
				headers: {
					'X-Chunk-Index': String(i),
					'X-Chunk-Total': String(total),
				},
				body: fd,
			});
			if (!res.ok) throw await parseError(res);
			loaded += blob.size;
			onProgress?.(loaded, file.size);
			last = (await res.json()) as UploadedFile;
		}
		if (!last) throw new ApiError(500, 'streaming upload returned no result');
		return last;
	},
};
