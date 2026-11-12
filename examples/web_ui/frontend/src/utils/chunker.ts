import type { TFunction } from 'i18next';

const SEPARATOR_LABELS: Record<string, string> = {
	'\n\n': 'paragraph',
	'\r\n\r\n': 'paragraph',
	'\n': 'newline',
	'\r\n': 'newline',
	'\r': 'carriageReturn',
	'\t': 'tab',
	'. ': 'periodSpace',
	'! ': 'exclamationSpace',
	'? ': 'questionSpace',
	'; ': 'semicolonSpace',
	', ': 'commaSpace',
	' ': 'space',
};

/** Return the separators used before the implicit character fallback. */
export function explicitChunkerSeparators(separators: string[]): string[] {
	const fallbackIndex = separators.indexOf('');
	return fallbackIndex === -1 ? separators : separators.slice(0, fallbackIndex);
}

/** Name invisible separators consistently in the editor and summary. */
export function formatChunkerSeparator(separator: string, t: TFunction): string {
	return Object.hasOwn(SEPARATOR_LABELS, separator)
		? t(`chunker-summary.labels.${SEPARATOR_LABELS[separator]}`)
		: JSON.stringify(separator).slice(1, -1).replace(/ /g, '␠');
}

/** Format persisted chunker parameters without losing control characters. */
export function formatChunkerParameterValue(value: unknown): string {
	if (Array.isArray(value)) {
		return `[${value.map((item) => JSON.stringify(item)).join(', ')}]`;
	}
	if (value !== null && typeof value === 'object') {
		return JSON.stringify(value);
	}
	return String(value);
}
