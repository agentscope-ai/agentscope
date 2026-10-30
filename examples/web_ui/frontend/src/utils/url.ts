export function normalizeServerUrl(value: string): string {
	const trimmed = value.trim();
	if (!trimmed) return '';

	if (/^[a-zA-Z][a-zA-Z\d+\-.]*:\/\//.test(trimmed)) {
		return trimmed;
	}

	return `http://${trimmed}`;
}
