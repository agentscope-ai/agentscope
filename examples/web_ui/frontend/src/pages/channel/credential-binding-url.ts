const DATA_IMAGE_URL =
	/^data:image\/(?:png|jpeg|gif|webp|svg\+xml)(?:;charset=[^;,]+)?(?:;base64)?,/i;

/** Return whether a QR image URL is safe for direct browser rendering. */
export function isSafeQrCodeUrl(value: string): boolean {
	if (DATA_IMAGE_URL.test(value)) return true;
	try {
		return new URL(value).protocol === 'https:';
	} catch {
		return false;
	}
}
