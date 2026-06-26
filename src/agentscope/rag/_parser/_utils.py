# -*- coding: utf-8 -*-
"""Shared helpers for binary parsers.

Three small utilities used by :class:`PDFParser`, :class:`ImageParser`,
and :class:`PPTParser`:

- :func:`_guess_image_media_type` — sniff the IANA media type from raw
  image bytes by looking at the magic number.  Used to populate the
  ``media_type`` field of :class:`Base64Source` for embedded images.
- :func:`_table_to_markdown` — render a 2-D ``list[list[str]]`` as a
  Markdown pipe-table; the default rendering for table content.
- :func:`_table_to_json` — render the same shape as a JSON array
  prefixed with a one-line ``<system-info>`` marker; used when the
  caller picks ``table_format="json"`` to avoid Markdown's
  multi-line-cell ambiguity.
"""
import json


def _guess_image_media_type(data: bytes) -> str:
    """Sniff the IANA media type of an image from its magic number.

    Args:
        data (`bytes`):
            The raw image bytes.

    Returns:
        `str`:
            The IANA media type (e.g. ``"image/png"``).  Falls back to
            ``"image/jpeg"`` when no signature matches — JPEG is the
            most permissive default and matches what every consumer can
            decode.
    """
    signatures = {
        b"\x89PNG\r\n\x1a\n": "image/png",
        b"\xff\xd8": "image/jpeg",
        b"GIF87a": "image/gif",
        b"GIF89a": "image/gif",
        b"BM": "image/bmp",
    }
    for signature, media_type in signatures.items():
        if data.startswith(signature):
            return media_type
    # WebP: ``RIFF`` at offset 0 + ``WEBP`` at offset 8.
    if len(data) > 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def _table_to_markdown(table_data: list[list[str]]) -> str:
    """Render a 2-D table as a Markdown pipe-table.

    Args:
        table_data (`list[list[str]]`):
            The table data; ``table_data[0]`` is the header row.

    Returns:
        `str`:
            The Markdown rendering, or the empty string when
            ``table_data`` is empty / column-less.
    """
    if not table_data:
        return ""

    num_cols = len(table_data[0])
    if num_cols == 0:
        return ""

    lines = [
        "| " + " | ".join(table_data[0]) + " |",
        "| " + " | ".join(["---"] * num_cols) + " |",
    ]
    for row in table_data[1:]:
        # Pad short rows so column counts match the header.
        padded = list(row) + [""] * max(0, num_cols - len(row))
        lines.append("| " + " | ".join(padded[:num_cols]) + " |")
    return "\n".join(lines) + "\n"


def _table_to_json(table_data: list[list[str]]) -> str:
    """Render a 2-D table as JSON prefixed by a one-line marker.

    The ``<system-info>`` marker lets the LLM (and any
    structure-aware downstream renderer) tell at a glance that this
    block is tabular data, not free text — which is otherwise
    indistinguishable from a raw JSON dump in the middle of a chunk.

    Args:
        table_data (`list[list[str]]`):
            The table data.

    Returns:
        `str`:
            ``"<system-info>...</system-info>\\n<json>"``.
    """
    return (
        "<system-info>A table loaded as a JSON array:</system-info>\n"
        + json.dumps(table_data, ensure_ascii=False)
    )
