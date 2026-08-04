import { ApiError, client, getBaseUrl, getUserId } from './client';
import type { UploadProgress } from './knowledgeBase';
import type {
	AddFromLibraryResponse,
	AddSkillRequest,
	MCPClient,
	MCPClientStatus,
	Skill,
} from './types';

export interface UploadOptions {
	/** Fired with byte-level progress while the body is streamed. */
	onProgress?: (progress: UploadProgress) => void;
	/** Aborts the upload; rejects with a `DOMException` named `AbortError`. */
	signal?: AbortSignal;
}

/**
 * XHR-based folder upload — `fetch` surfaces no byte-level send
 * progress, so anything driving a progress bar has to use XHR.
 *
 * The manifest rides alongside the parts because the server builds a
 * tar as they arrive, and a tar header needs each member's size before
 * its bytes — which a multipart part does not declare.
 */
function uploadSkillXhr(
	agentId: string,
	sessionId: string,
	files: File[],
	options: UploadOptions = {},
): Promise<void> {
	const { onProgress, signal } = options;
	const formData = new FormData();
	formData.append(
		'manifest',
		JSON.stringify({
			entries: files.map((file) => ({
				path: file.webkitRelativePath || file.name,
				size: file.size,
			})),
		}),
	);
	for (const file of files) formData.append('files', file);

	return new Promise((resolve, reject) => {
		if (signal?.aborted) {
			reject(new DOMException('Aborted', 'AbortError'));
			return;
		}

		const xhr = new XMLHttpRequest();
		const url = new URL('/workspace/skill/upload', getBaseUrl());
		url.searchParams.set('agent_id', agentId);
		url.searchParams.set('session_id', sessionId);
		xhr.open('POST', url.toString(), true);
		xhr.setRequestHeader('X-User-ID', getUserId());

		const onAbort = () => xhr.abort();
		signal?.addEventListener('abort', onAbort, { once: true });
		const cleanup = () => signal?.removeEventListener('abort', onAbort);

		if (xhr.upload && onProgress) {
			xhr.upload.onprogress = (e) => {
				onProgress({
					loaded: e.loaded,
					total: e.lengthComputable ? e.total : 0,
				});
			};
		}

		xhr.onload = () => {
			cleanup();
			if (xhr.status >= 200 && xhr.status < 300) {
				resolve();
				return;
			}
			let detail = xhr.responseText || xhr.statusText;
			try {
				const json = JSON.parse(xhr.responseText) as { detail?: unknown };
				if (typeof json.detail === 'string') detail = json.detail;
				else if (json.detail !== undefined) detail = JSON.stringify(json.detail);
			} catch {
				// keep raw text
			}
			reject(new ApiError(xhr.status, detail));
		};
		xhr.onerror = () => {
			cleanup();
			reject(new ApiError(0, 'Network error'));
		};
		xhr.onabort = () => {
			cleanup();
			reject(new DOMException('Aborted', 'AbortError'));
		};

		xhr.send(formData);
	});
}

export const workspaceApi = {
	mcp: {
		list: (agentId: string, sessionId: string) =>
			client.get<MCPClientStatus[]>('/workspace/mcp', {
				agent_id: agentId,
				session_id: sessionId,
			}),

		add: (agentId: string, sessionId: string, mcp: MCPClient) =>
			client.post<void>('/workspace/mcp', mcp, { agent_id: agentId, session_id: sessionId }),

		/**
		 * Puts MCPs the user has already installed into this workspace.
		 * Ids, not configs — the rendered config never leaves the server,
		 * so the client has no way to reconstruct one.
		 */
		addFromLibrary: (agentId: string, sessionId: string, mcpIds: string[]) =>
			client.post<AddFromLibraryResponse>(
				'/workspace/mcp/from-library',
				{ mcp_ids: mcpIds },
				{ agent_id: agentId, session_id: sessionId },
			),

		remove: (mcpName: string, agentId: string, sessionId: string) =>
			client.delete(`/workspace/mcp/${mcpName}`, {
				agent_id: agentId,
				session_id: sessionId,
			}),
	},

	skill: {
		list: (agentId: string, sessionId: string) =>
			client.get<Skill[]>('/workspace/skill', { agent_id: agentId, session_id: sessionId }),

		/**
		 * @deprecated The path is resolved on the server. Use `upload`
		 * for a local folder or `addFromLibrary` for an installed skill.
		 */
		add: (agentId: string, sessionId: string, body: AddSkillRequest) =>
			client.post<void>('/workspace/skill', body, {
				agent_id: agentId,
				session_id: sessionId,
			}),

		/** Uploads a picked folder as a skill, reporting send progress. */
		upload: (agentId: string, sessionId: string, files: File[], options: UploadOptions = {}) =>
			uploadSkillXhr(agentId, sessionId, files, options),

		/** Installs skills the user already has, by library record id. */
		addFromLibrary: (agentId: string, sessionId: string, skillIds: string[]) =>
			client.post<AddFromLibraryResponse>(
				'/workspace/skill/from-library',
				{ skill_ids: skillIds },
				{ agent_id: agentId, session_id: sessionId },
			),

		remove: (skillName: string, agentId: string, sessionId: string) =>
			client.delete(`/workspace/skill/${skillName}`, {
				agent_id: agentId,
				session_id: sessionId,
			}),
	},
};
