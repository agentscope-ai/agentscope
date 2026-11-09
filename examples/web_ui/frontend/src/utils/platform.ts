export type Platform = 'darwin' | 'win32' | 'linux';

/**
 * Gets the current platform (darwin, win32, or linux).
 *
 * @returns The platform identifier.
 */
export function getPlatform(): Platform {
	// Use modern userAgentData API if available
	const uaData = (navigator as Navigator & { userAgentData?: { platform: string } })
		.userAgentData;
	if (uaData?.platform) {
		const platform = uaData.platform.toLowerCase();
		if (platform.includes('mac')) return 'darwin';
		if (platform.includes('win')) return 'win32';
		return 'linux';
	}

	// Fallback to userAgent string
	const userAgent = navigator.userAgent.toLowerCase();
	if (userAgent.includes('mac')) return 'darwin';
	if (userAgent.includes('win')) return 'win32';
	return 'linux';
}

/**
 * Checks if the current platform is macOS.
 *
 * @returns True if the platform is macOS, false otherwise.
 */
export function isMac(): boolean {
	return getPlatform() === 'darwin';
}
