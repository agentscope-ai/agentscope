# -*- coding: utf-8 -*-
"""Shared transport and validation for voice creation clients."""
from typing import Any
import mimetypes

import httpx


class VoiceCloningError(RuntimeError):
    """A voice creation provider failed or returned an invalid response."""


def _audio_file(filename: str, audio: bytes) -> tuple[str, bytes, str]:
    """Validate an audio upload and infer its MIME type from the filename."""
    if not audio:
        raise ValueError("audio must not be empty")
    if not filename:
        raise ValueError("filename must not be empty")
    mime_type, _ = mimetypes.guess_type(filename)
    return filename, audio, mime_type or "audio/wav"


async def _post_voice_request(
    url: str,
    *,
    provider: str,
    operation: str,
    success_codes: tuple[int, ...] = (200,),
    **kwargs: Any,
) -> dict:
    """Send one creation request without retrying non-idempotent operations."""
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(url, **kwargs)
    except httpx.HTTPError as exc:
        raise VoiceCloningError(
            f"Network error calling {provider}: {exc}",
        ) from exc
    if response.status_code not in success_codes:
        raise VoiceCloningError(
            f"{provider} {operation} failed "
            f"({response.status_code}): {response.text[:500]}",
        )
    try:
        result = response.json()
    except ValueError as exc:
        raise VoiceCloningError(
            f"{provider} returned an invalid JSON response.",
        ) from exc
    if not isinstance(result, dict):
        raise VoiceCloningError(
            f"{provider} returned an invalid JSON response.",
        )
    return result
