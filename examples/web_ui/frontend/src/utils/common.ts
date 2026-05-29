/**
 * Copy a string to the system clipboard.
 *
 * @param text The text content to copy.
 * @returns A promise that resolves to true if the copy succeeds; false otherwise.
 */
export const copyToClipboard = async (text: string): Promise<boolean> => {
	try {
		await navigator.clipboard.writeText(text);
		return true;
	} catch (err) {
		console.error('Failed to copy text: ', err);
		return false;
	}
};

/**
 * Format a number to a human-readable string with commas and suffixes
 * @param num - The number to format
 * @returns Formatted string (e.g., "1,000", "10.2k", "1.5M")
 */
export function formatNumber(num: number): string {
	if (num < 1000) {
		return num.toLocaleString();
	}

	const units = [
		{ value: 1e9, suffix: 'B' },
		{ value: 1e6, suffix: 'M' },
		{ value: 1e3, suffix: 'k' },
	];

	for (const { value, suffix } of units) {
		if (num >= value) {
			const formatted = num / value;
			// Keep 1-2 decimal places, remove trailing zeros
			const decimals = formatted >= 10 ? 1 : 2;
			return formatted.toFixed(decimals).replace(/\.0+$/, '') + suffix;
		}
	}

	return num.toLocaleString();
}

/**
 * Format a duration in seconds into a human-readable string with appropriate units.
 * Converts seconds to milliseconds for values less than 1 second.
 *
 * @param seconds - The duration in seconds to format
 * @returns Formatted string with unit (e.g., "500.00ms" or "2.50s")
 */
export const formatDurationWithUnit = (seconds: number): string => {
	if (seconds < 1) {
		return `${(seconds * 1000).toFixed(2)}ms`;
	}
	return `${seconds.toFixed(2)}s`;
};

/**
 * Format a duration in seconds into a numeric value with appropriate scaling.
 * Converts seconds to milliseconds for values less than 1 second.
 *
 * @param seconds - The duration in seconds to format
 * @returns Formatted number (in milliseconds if < 1 second, otherwise in seconds)
 */
export const formatDuration = (seconds: number): number => {
	if (seconds < 1) {
		return parseFloat((seconds * 1000).toFixed(2));
	}
	return parseFloat(seconds.toFixed(2));
};

/**
 * Format a duration in seconds into a human-readable, compact string.
 *
 * - Integer-only output (e.g. `45s`, `2min30s`, `3h`); no decimals.
 * - Adjacent unit segments are concatenated without spaces.
 * - Once the leading unit is hours, only whole hours are shown — minute and
 *   second precision is not useful at that scale and would only widen the
 *   badge when, e.g., a tool call sits awaiting user confirmation for hours.
 *
 * @param seconds - The duration in seconds to format
 * @returns Formatted string (e.g., "45s", "2min30s", "3h")
 */
export const formatTime = (seconds: number): string => {
	const total = Math.floor(seconds);
	if (total < 60) {
		return `${total}s`;
	}
	if (total < 3600) {
		const minutes = Math.floor(total / 60);
		const remaining = total % 60;
		return remaining === 0 ? `${minutes}min` : `${minutes}min${remaining}s`;
	}
	return `${Math.floor(total / 3600)}h`;
};
