# -*- coding: utf-8 -*-
"""Internal helpers for incrementally merging Base64 data."""
import base64


_BASE64_CHARS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/",
)


def _is_base64_shape(value: str) -> bool:
    """Return whether a string has the shape of standard Base64."""
    if len(value) % 4:
        return False

    padding = len(value) - len(value.rstrip("="))
    if padding > 2:
        return False

    return all(char in _BASE64_CHARS for char in value.rstrip("="))


def _append_base64_chunk(
    existing: str,
    incoming: str,
    *,
    validate: bool = False,
) -> str:
    """Append an independently encoded Base64 chunk efficiently.

    Valid, unpadded Base64 can be concatenated with a newly encoded chunk.
    When the existing value has padding, only its final quantum needs to be
    decoded and re-encoded with the incoming bytes.
    """
    if existing and not _is_base64_shape(existing):
        existing_bytes = base64.b64decode(existing, validate=validate)
        incoming_bytes = base64.b64decode(incoming, validate=validate)
        return base64.b64encode(existing_bytes + incoming_bytes).decode(
            "ascii"
        )

    incoming_bytes = base64.b64decode(incoming, validate=validate)
    encoded_incoming = base64.b64encode(incoming_bytes).decode("ascii")

    if not existing:
        return encoded_incoming

    if not existing.endswith("="):
        return existing + encoded_incoming

    tail = base64.b64decode(existing[-4:], validate=validate)
    return existing[:-4] + base64.b64encode(tail + incoming_bytes).decode(
        "ascii",
    )
