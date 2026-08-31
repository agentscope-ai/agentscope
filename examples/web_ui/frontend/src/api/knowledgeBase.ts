import { ApiError, client, getBaseUrl, getUserId } from './client';
import type {
	CreateKnowledgeBaseRequest,
	CreateKnowledgeBaseResponse,
	DocumentDownloadTokenResponse,
	KbMiddlewareParametersSchemaResponse,
	KnowledgeBaseView,
	ListChunkersResponse,
	ListDocumentChunksResponse,
	ListKbEmbeddingModelsResponse,
	ListKnowledgeBasesParams,
	ListKnowledgeBasesResponse,
	ListKnowledgeDocumentsParams,
	ListKnowledgeDocumentsResponse,
	ListKnowledgeDocumentStatusResponse,
	ListSupportedContentTypesResponse,
	SearchKnowledgeBaseRequest,
	SearchKnowledgeBaseResponse,
	UpdateKnowledgeBaseRequest,
	UploadKnowledgeDocumentResponse,
} from './types';

/** Drop undefined values and stringify the rest for `client` params. */
function toQuery(params: Record<string, unknown>): Record<string, string> {
	const query: Record<string, string> = {};
	for (const [key, value] of Object.entries(params)) {
		if (value !== undefined) query[key] = String(value);
	}
	return query;
}

/** The backend caps `page_size` at 128. */
const MAX_PAGE_SIZE = 128;

/**
 * Drain a paginated endpoint into one flat array — genuinely all of
 * it, however many pages that takes.
 *
 * Keeps requesting pages while the accumulated count is below the
 * server-reported `total` and the last page still made progress. An
 * empty page means the server has nothing more to give, so a lying
 * `total` degrades into a clean stop instead of an infinite loop —
 * no arbitrary page cap that would silently truncate large lists.
 */
async function fetchAllPages<T>(
	fetchPage: (page: number, pageSize: number) => Promise<{ items: T[]; total: number }>,
): Promise<T[]> {
	const all: T[] = [];
	for (let page = 1; ; page++) {
		const { items, total } = await fetchPage(page, MAX_PAGE_SIZE);
		all.push(...items);
		if (items.length === 0 || all.length >= total) break;
	}
	return all;
}

/**
 * Callback invoked while bytes are pushed across the wire.
 *
 * - `loaded` — bytes already sent.
 * - `total` — total bytes (may be 0 when the browser cannot compute it,
 *   e.g. for chunked encodings).
 */
export interface UploadProgress {
	loaded: number;
	total: number;
}

export interface UploadDocumentOptions {
	/** Fired with byte-level progress while the body is streamed. */
	onProgress?: (progress: UploadProgress) => void;
	/**
	 * Caller-supplied abort signal. Aborting before the server has
	 * responded rejects the returned promise with a `DOMException` of
	 * `name === "AbortError"`; aborting after a response has come back
	 * is a no-op.
	 */
	signal?: AbortSignal;
}

/**
 * XHR-based upload — `fetch` does not surface byte-level send
 * progress in any current browser, so multipart uploads that drive a
 * progress UI have to fall back to XMLHttpRequest.
 */
function uploadDocumentXhr(
	knowledgeBaseId: string,
	file: File,
	options: UploadDocumentOptions = {},
): Promise<UploadKnowledgeDocumentResponse> {
	const { onProgress, signal } = options;
	const formData = new FormData();
	formData.append('file', file);

	return new Promise((resolve, reject) => {
		if (signal?.aborted) {
			reject(new DOMException('Aborted', 'AbortError'));
			return;
		}

		const xhr = new XMLHttpRequest();
		const url = new URL(`/knowledge_bases/${knowledgeBaseId}/documents`, getBaseUrl());
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
				try {
					resolve(JSON.parse(xhr.responseText) as UploadKnowledgeDocumentResponse);
				} catch (e) {
					reject(e);
				}
				return;
			}
			let detail = xhr.responseText || xhr.statusText;
			try {
				const json = JSON.parse(xhr.responseText) as {
					detail?: unknown;
				};
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

/**
 * Client for the `/knowledge_bases` router.
 */
export const knowledgeBaseApi = {
	list: (params: ListKnowledgeBasesParams = {}) =>
		client.get<ListKnowledgeBasesResponse>('/knowledge_bases/', toQuery({ ...params })),

	/**
	 * Fetch every visible knowledge base across all pages — for views
	 * that render one flat list (e.g. the sidebar).
	 */
	listAll: (params: Omit<ListKnowledgeBasesParams, 'page' | 'page_size'> = {}) =>
		fetchAllPages(async (page, pageSize) => {
			const res = await knowledgeBaseApi.list({
				...params,
				page,
				page_size: pageSize,
			});
			return { items: res.knowledge_bases, total: res.total };
		}),

	listEmbeddingModels: () =>
		client.get<ListKbEmbeddingModelsResponse>('/knowledge_bases/embedding_models'),

	listChunkers: () => client.get<ListChunkersResponse>('/knowledge_bases/chunkers'),

	/** Fetch the JSON Schema describing the KB middleware's tunable params. */
	middlewareParametersSchema: () =>
		client.get<KbMiddlewareParametersSchemaResponse>(
			'/knowledge_bases/middleware/parameters_schema',
		),

	/** List the union of media types + extensions every parser accepts. */
	supportedContentTypes: () =>
		client.get<ListSupportedContentTypesResponse>('/knowledge_bases/supported_content_types'),

	create: (body: CreateKnowledgeBaseRequest) =>
		client.post<CreateKnowledgeBaseResponse>('/knowledge_bases/', body),

	update: (knowledgeBaseId: string, body: UpdateKnowledgeBaseRequest) =>
		client.patch<KnowledgeBaseView>(`/knowledge_bases/${knowledgeBaseId}`, body),

	delete: (knowledgeBaseId: string) => client.delete(`/knowledge_bases/${knowledgeBaseId}`),

	/** List one page of the documents registered against a knowledge base. */
	listDocuments: (knowledgeBaseId: string, params: ListKnowledgeDocumentsParams = {}) =>
		client.get<ListKnowledgeDocumentsResponse>(
			`/knowledge_bases/${knowledgeBaseId}/documents`,
			toQuery({ ...params }),
		),

	/**
	 * Fetch every document of a knowledge base across all pages — for
	 * views that render one flat list (e.g. the documents panel).
	 */
	listAllDocuments: (
		knowledgeBaseId: string,
		params: Omit<ListKnowledgeDocumentsParams, 'page' | 'page_size'> = {},
	) =>
		fetchAllPages(async (page, pageSize) => {
			const res = await knowledgeBaseApi.listDocuments(knowledgeBaseId, {
				...params,
				page,
				page_size: pageSize,
			});
			return { items: res.documents, total: res.total };
		}),

	/** Browse one page of a document's chunks in `chunk_index` order. */
	listDocumentChunks: (knowledgeBaseId: string, documentId: string, page = 1, pageSize = 30) =>
		client.get<ListDocumentChunksResponse>(
			`/knowledge_bases/${knowledgeBaseId}/documents/${documentId}/chunks`,
			toQuery({ page, page_size: pageSize }),
			// 501 means the configured vector store cannot list chunks —
			// the caller hides the chunk view instead of toasting.
			{ silent: true },
		),

	/**
	 * Mint a short-lived token so a browser-native fetch (`<iframe>`,
	 * `<img>`, a download navigation) can retrieve the raw file
	 * without the `X-User-ID` header.
	 */
	createDocumentDownloadToken: (knowledgeBaseId: string, documentId: string) =>
		client.post<DocumentDownloadTokenResponse>(
			`/knowledge_bases/${knowledgeBaseId}/documents/${documentId}/download_token`,
		),

	/**
	 * Absolute URL of a document's raw bytes, using a token minted via
	 * `createDocumentDownloadToken`. Pass `download: true` to force a
	 * `Content-Disposition: attachment`.
	 */
	documentContentUrl: (
		knowledgeBaseId: string,
		documentId: string,
		token: string,
		download = false,
	) => {
		const url = new URL(
			`/knowledge_bases/${knowledgeBaseId}/documents/${documentId}`,
			getBaseUrl(),
		);
		url.searchParams.set('token', token);
		if (download) url.searchParams.set('download', 'true');
		return url.toString();
	},

	/**
	 * Fetch the raw file as text through the authenticated client —
	 * for markdown / plain-text previews that render in-app.
	 */
	fetchDocumentText: async (knowledgeBaseId: string, documentId: string) => {
		const res = await client.stream(
			`/knowledge_bases/${knowledgeBaseId}/documents/${documentId}`,
		);
		return res.text();
	},

	/**
	 * Batch-query lifecycle status for a list of documents.
	 *
	 * Missing ids are silently omitted by the server, so the response
	 * may be shorter than the input. An empty `ids` short-circuits
	 * locally — the backend treats an empty list as a 200 with
	 * `items: []`, but skipping the round-trip is friendlier to the
	 * polling loop.
	 */
	getDocumentStatus: (knowledgeBaseId: string, ids: string[]) => {
		if (ids.length === 0) {
			return Promise.resolve<ListKnowledgeDocumentStatusResponse>({
				items: [],
			});
		}
		return client.get<ListKnowledgeDocumentStatusResponse>(
			`/knowledge_bases/${knowledgeBaseId}/documents/status`,
			{ ids: ids.join(',') },
		);
	},

	uploadDocument: (knowledgeBaseId: string, file: File, options?: UploadDocumentOptions) =>
		uploadDocumentXhr(knowledgeBaseId, file, options),

	deleteDocument: (knowledgeBaseId: string, documentId: string) =>
		client.delete(`/knowledge_bases/${knowledgeBaseId}/documents/${documentId}`),

	search: (knowledgeBaseId: string, body: SearchKnowledgeBaseRequest) =>
		client.post<SearchKnowledgeBaseResponse>(
			`/knowledge_bases/${knowledgeBaseId}/search`,
			body,
		),
};
