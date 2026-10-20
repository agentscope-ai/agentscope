import type { ContentBlock, ToolCallBlock } from '@agentscope-ai/agentscope/message';

export interface FileTarget {
	path: string;
	name: string;
}

export interface FileArtifact extends FileTarget {
	change: 'written' | 'modified';
}

/** Append a backend directory entry without interpreting it as a browser URL. */
export function workspaceChildPath(directory: string, name: string): string {
	const windows = /^[a-z]:[\\/]/i.test(directory) || directory.startsWith('\\\\');
	const separator = windows && directory.includes('\\') ? '\\' : '/';
	return `${directory}${directory.endsWith(separator) ? '' : separator}${name}`;
}

/** Keep operation IDs so repeated changes to one file remain distinguishable. */
export function extractFileChanges(content: ContentBlock[]): (FileArtifact & { id: string })[] {
	const calls = new Map<string, ToolCallBlock>();
	for (const block of content) {
		if (block.type === 'tool_call') calls.set(block.id, block);
	}
	const changes: (FileArtifact & { id: string })[] = [];
	for (const result of content) {
		if (result.type !== 'tool_result' || result.state !== 'success') continue;
		const call = calls.get(result.id);
		if (!call || (call.name !== 'Write' && call.name !== 'Edit')) continue;
		let input: unknown;
		try {
			input = JSON.parse(call.input);
		} catch {
			continue;
		}
		if (!input || typeof input !== 'object' || !('file_path' in input)) continue;
		const path = input.file_path;
		if (typeof path !== 'string' || !path.trim() || path.includes('\0')) continue;
		// History does not retain a relative path's original working directory.
		// Accept POSIX roots, Windows drive roots and complete UNC paths only.
		if (
			!path.startsWith('/') &&
			!/^[a-z]:[\\/]/i.test(path) &&
			!/^\\\\[^\\]+\\[^\\]+(?:\\|$)/.test(path)
		)
			continue;
		// Keep backend paths verbatim: %, # and backslashes are not URL syntax.
		changes.push({
			id: call.id,
			path,
			name: path.split(/[/\\]/).pop() || path,
			change: call.name === 'Write' ? 'written' : 'modified',
		});
	}
	return changes;
}

/** Derive current-file references from successful built-in file operations. */
export function extractArtifacts(content: ContentBlock[]): FileArtifact[] {
	const artifacts = new Map<string, FileArtifact>();
	for (const { path, name, change } of extractFileChanges(content)) {
		artifacts.delete(path);
		artifacts.set(path, { path, name, change });
	}
	return [...artifacts.values()].reverse();
}

export type FileChanges = ReadonlyMap<string, number>;

/** Compare backend paths lexically, independent of the browser's OS. */
export function filePathKey(path: string): string {
	const windows = /^[a-z]:[\\/]/i.test(path) || path.startsWith('\\\\');
	const value = windows ? path.replace(/\\/g, '/').toLowerCase() : path;
	const prefix = value.startsWith('//') && windows ? '//' : value.startsWith('/') ? '/' : '';
	const rootParts = windows ? (prefix === '//' ? 2 : 1) : 0;
	const parts: string[] = [];
	for (const part of value.split('/')) {
		if (!part || part === '.') continue;
		if (part === '..') {
			if (parts.length > rootParts) parts.pop();
		} else parts.push(part);
	}
	return `${prefix}${parts.join('/')}`;
}

export function fileChangeVersion(changes: FileChanges, path: string, descendants = false): number {
	const key = filePathKey(path);
	const prefix = key.endsWith('/') ? key : `${key}/`;
	let version = changes.get(key) ?? 0;
	if (descendants) {
		for (const [target, changed] of changes) {
			if (target.startsWith(prefix)) version = Math.max(version, changed);
		}
	}
	return version;
}

export type PreviewKind = 'markdown' | 'html' | 'csv' | 'text' | 'image' | 'pdf' | 'binary';

export function previewKind(path: string): PreviewKind {
	const name = path.split(/[/\\]/).pop()?.toLowerCase() ?? '';
	const extension = name.split('.').pop() ?? '';
	if (['md', 'markdown', 'mdx'].includes(extension)) return 'markdown';
	if (['html', 'htm'].includes(extension)) return 'html';
	if (extension === 'csv') return 'csv';
	if (extension === 'pdf') return 'pdf';
	if (['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'svg', 'ico', 'avif'].includes(extension)) {
		return 'image';
	}
	if (
		[
			'txt',
			'log',
			'json',
			'jsonl',
			'yaml',
			'yml',
			'toml',
			'xml',
			'ini',
			'cfg',
			'conf',
			'py',
			'js',
			'jsx',
			'ts',
			'tsx',
			'css',
			'less',
			'scss',
			'java',
			'go',
			'rs',
			'c',
			'h',
			'cpp',
			'hpp',
			'cs',
			'rb',
			'php',
			'sh',
			'bash',
			'zsh',
			'ps1',
			'bat',
			'cmd',
			'sql',
			'r',
			'swift',
			'kt',
			'lua',
			'vue',
			'svelte',
			'tex',
			'env',
			'gitignore',
			'dockerignore',
		].includes(extension) ||
		['dockerfile', 'makefile', 'license', 'readme'].includes(name)
	)
		return 'text';
	return 'binary';
}

export const TEXT_PREVIEW_LIMIT = 2 * 1024 * 1024;
export const BINARY_PREVIEW_LIMIT = 20 * 1024 * 1024;

export class PreviewTooLargeError extends Error {
	constructor() {
		super('File exceeds the preview limit.');
		this.name = 'PreviewTooLargeError';
	}
}

/** Enforce the limit while reading, including responses without a length. */
export async function readPreviewBlob(response: Response, limit: number): Promise<Blob> {
	if (Number(response.headers.get('Content-Length')) > limit) {
		await response.body?.cancel();
		throw new PreviewTooLargeError();
	}
	if (!response.body) return new Blob();
	const reader = response.body.getReader();
	const chunks: Uint8Array<ArrayBuffer>[] = [];
	let size = 0;
	try {
		while (true) {
			const { done, value } = await reader.read();
			if (done) break;
			size += value.byteLength;
			if (size > limit) throw new PreviewTooLargeError();
			chunks.push(new Uint8Array(value));
		}
		return new Blob(chunks);
	} catch (error) {
		await reader.cancel().catch(() => {});
		throw error;
	} finally {
		reader.releaseLock();
	}
}
