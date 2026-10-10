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
