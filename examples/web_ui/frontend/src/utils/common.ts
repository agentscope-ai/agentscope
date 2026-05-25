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
 * Format a duration in seconds into a human-readable string with appropriate units (seconds, minutes, hours).
 *
 * @param seconds - The duration in seconds to format
 * @param decimals - The number of decimal places to show (default: 1)
 * @returns Formatted string with appropriate time unit (e.g., "45.2s", "2m 30.0s", "1h 15m 30.5s")
 */
export const formatTime = (seconds: number, decimals: number = 1): string => {
	// If duration is less than 60 seconds, display in seconds with specified decimal places
	if (seconds < 60) {
		return `${seconds.toFixed(decimals)}s`;
	}
	// If duration is between 60 seconds and 1 hour, display in minutes and seconds
	else if (seconds < 3600) {
		const minutes = Math.floor(seconds / 60);
		const remainingSeconds = seconds % 60;
		// If there are no remaining seconds, only show minutes
		if (remainingSeconds === 0) {
			return `${minutes}min`;
		}
		// Otherwise, show both minutes and seconds with specified decimal places
		return `${minutes}min ${remainingSeconds.toFixed(decimals)}s`;
	}
	// If duration is 1 hour or more, display in hours, minutes, and seconds
	else {
		const hours = Math.floor(seconds / 3600);
		const minutes = Math.floor((seconds % 3600) / 60);
		const remainingSeconds = seconds % 60;

		// Only show hours if minutes and seconds are both zero
		if (minutes === 0 && remainingSeconds === 0) {
			return `${hours}h`;
		}
		// Show hours and minutes if seconds is zero
		else if (remainingSeconds === 0) {
			return `${hours}h ${minutes}min`;
		}
		// Show hours and seconds if minutes is zero
		else if (minutes === 0) {
			return `${hours}h ${remainingSeconds.toFixed(decimals)}s`;
		}
		// Show all components: hours, minutes, and seconds
		return `${hours}h ${minutes}min ${remainingSeconds.toFixed(decimals)}s`;
	}
};
