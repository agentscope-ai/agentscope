import { buildApiUrl, getUserId, ApiError } from './client';

//
// API client for the BocomADP file-upload capability
// (backend: bocomadp/routers/uploads.py, prefix /files).
// The /api prefix is added by buildApiUrl and stripped by the nginx gateway.
//
// Endpoints:
//   1. sync upload POST /api/files/upload          (multipart: file, agent_id, session_id)
//   2. limits      GET  /api/files/limits          (upload capability / thresholds)
// Plus listing / delete / download.
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

/**
 * Files handled server-side by the BocomADP upload endpoint:
 * convertible docs (→ Markdown outline) and images (→ base64 fixed into
 * uploads metadata, read by the view_image_tool). Both are referenced via
 * the `files` field instead of being inlined into the message body.
 */
export function isServerProcessedFile(file: File): boolean {
	if (file.type.startsWith('image/')) return true;
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
		const res = await fetch(buildApiUrl('/files/limits'));
		if (!res.ok) throw await parseError(res);
		return (await res.json()) as UploadLimits;
	},

	async list(agentId: string, sessionId: string): Promise<UploadedFile[]> {
		const url = buildApiUrl('/files/uploads');
		url.searchParams.set('agent_id', agentId);
		url.searchParams.set('session_id', sessionId);
		const res = await fetch(url, { headers: { 'X-User-ID': getUserId() } });
		if (!res.ok) throw await parseError(res);
		const body = (await res.json()) as { files: UploadedFile[] };
		return body.files;
	},

	async delete(agentId: string, sessionId: string, filename: string): Promise<void> {
		const url = buildApiUrl('/files/upload');
		url.searchParams.set('agent_id', agentId);
		url.searchParams.set('session_id', sessionId);
		url.searchParams.set('filename', filename);
		const res = await fetch(url, {
			method: 'DELETE',
			headers: { 'X-User-ID': getUserId() },
		});
		if (!res.ok) throw await parseError(res);
	},

	/** Upload a file to the server, returning its virtual path ref. */
	async upload(
		agentId: string,
		sessionId: string,
		file: File,
		onProgress?: (loaded: number, total: number) => void,
	): Promise<UploadedFile> {
		const fd = new FormData();
		fd.append('file', file);
		fd.append('agent_id', agentId);
		fd.append('session_id', sessionId);

		const res = await fetch(buildApiUrl('/files/upload'), {
			method: 'POST',
			headers: { 'X-User-ID': getUserId() },
			body: fd,
		});
		if (!res.ok) throw await parseError(res);
		onProgress?.(file.size, file.size);
		return (await res.json()) as UploadedFile;
	},
};

