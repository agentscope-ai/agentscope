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
 * - The same reasoning extends past a day: only the leading whole unit is
 *   shown, up to years. Months and years use average lengths, so they are
 *   approximate by construction — fine for "updated 3mo ago", wrong for
 *   anything that needs calendar accuracy.
 *
 * @param seconds - The duration in seconds to format
 * @param options.leadingUnitOnly - Drop the trailing seconds from the
 *   minutes case, so `49m33s` reads `49m`. Defaults to `false`, keeping the
 *   two-segment form the elapsed-time badges rely on.
 * @returns Formatted string (e.g., "45s", "2min30s", "3h", "5d", "2y")
 */
export const formatTime = (
	seconds: number,
	options: { leadingUnitOnly?: boolean } = {},
): string => {
	const total = Math.floor(seconds);
	if (total < 60) {
		return `${total}s`;
	}
	if (total < 3600) {
		const minutes = Math.floor(total / 60);
		const remaining = total % 60;
		if (options.leadingUnitOnly || remaining === 0) {
			return `${minutes}m`;
		}
		return `${minutes}m${remaining}s`;
	}
	if (total < 86400) {
		return `${Math.floor(total / 3600)}h`;
	}
	// 30.44 and 365.25 days — the average month and year, so a "1y" does
	// not flip back to "12mo" for the leap-day stragglers.
	if (total < 2629746) {
		return `${Math.floor(total / 86400)}d`;
	}
	if (total < 31556952) {
		return `${Math.floor(total / 2629746)}mo`;
	}
	return `${Math.floor(total / 31556952)}y`;
};

/**
 * A deterministic, readable colour pair for a fallback avatar.
 *
 * The hue is hashed from the name so a given hub or card always looks
 * the same; lightness and chroma are fixed. Working in OKLCH is what
 * makes the fixed lightness safe — its L is perceptual, so every hue
 * lands on the same contrast ratio (~7:1 here). The same two numbers in
 * HSL would leave yellow washed out where blue reads fine.
 *
 * @param seed - Stable identity, e.g. the card or hub name.
 * @returns Inline styles pairing a tinted background with a much darker
 *   glyph of the same hue.
 */
export const avatarTint = (seed: string): { backgroundColor: string; color: string } => {
	// FNV-1a: cheap, and spreads adjacent names across the wheel rather
	// than clustering them the way a plain sum of char codes does.
	let hash = 0x811c9dc5;
	for (let i = 0; i < seed.length; i++) {
		hash ^= seed.charCodeAt(i);
		hash = Math.imul(hash, 0x01000193);
	}
	const hue = Math.abs(hash) % 360;
	// Verified across all 360 hues: 7.16:1 worst case (WCAG AAA), and the
	// chroma is low enough that nothing meaningfully leaves sRGB.
	return {
		backgroundColor: `oklch(0.94 0.03 ${hue})`,
		color: `oklch(0.41 0.075 ${hue})`,
	};
};
