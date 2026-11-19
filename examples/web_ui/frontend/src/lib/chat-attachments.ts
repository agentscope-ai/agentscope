import type { TextBlock } from '@agentscope-ai/agentscope/message';

const TEXT_EXTENSIONS = ['.txt', '.md', '.markdown'];
const TEXT_MEDIA_TYPES = new Set(['text/plain', 'text/markdown', 'text/x-markdown']);

/** Text models receive Markdown as text, so the picker must also accept its extensions. */
export function getChatAttachmentInputTypes(modelInputTypes: readonly string[]): string[] {
	const accepted = modelInputTypes.filter(
		(type) =>
			/^(image|video|audio|text)\/.+/.test(type) ||
			type === 'application/pdf' ||
			type.startsWith('application/vnd.') ||
			type.startsWith('application/msword'),
	);
	if (modelInputTypes.some((type) => type === 'text/plain' || type === 'text/*')) {
		accepted.push(...TEXT_EXTENSIONS, 'text/markdown', 'text/x-markdown');
	}
	return [...new Set(accepted)];
}

/** Read textual attachments before considering native paths or binary data blocks. */
export async function readChatTextAttachment(
	file: Pick<File, 'name' | 'type' | 'text'>,
): Promise<TextBlock | null> {
	const mediaType = file.type.split(';', 1)[0].trim().toLowerCase();
	const name = file.name.toLowerCase();
	if (!TEXT_MEDIA_TYPES.has(mediaType) && !TEXT_EXTENSIONS.some((ext) => name.endsWith(ext))) {
		return null;
	}
	const text = await file.text();
	return {
		id: crypto.randomUUID(),
		type: 'text',
		text: `[File: ${file.name}]\n${text}`,
		created_at: new Date().toISOString(),
	};
}
