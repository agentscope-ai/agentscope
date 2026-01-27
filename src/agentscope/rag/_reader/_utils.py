# -*- coding: utf-8 -*-
"""The image reader to read and chunk image files."""


def _get_media_type_from_data(data: bytes) -> str:
    """Determine media type from image data.

    Args:
        data (`bytes`):
            The raw image data.

    Returns:
        `str`:
            The MIME type of the image (e.g., "image/png", "image/jpeg").
    """
    # Image signature mapping
    signatures = {
        b"\x89PNG\r\n\x1a\n": "image/png",
        b"\xff\xd8": "image/jpeg",
        b"GIF87a": "image/gif",
        b"GIF89a": "image/gif",
        b"BM": "image/bmp",
    }

    # Check signatures
    for signature, media_type in signatures.items():
        if data.startswith(signature):
            return media_type

    # Check WebP (RIFF at start + WEBP at offset 8)
    if len(data) > 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"

    # Default to JPEG
    return "image/jpeg"
