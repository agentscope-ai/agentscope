# -*- coding: utf-8 -*-
"""Utility functions for RAG readers."""
import json


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


def _table_to_json(table_data: list[list[str]]) -> str:
    """Convert table data to JSON string.

    Args:
        table_data (`list[list[str]]`):
            Table data represented as a 2D list.

    Returns:
        `str`:
            A JSON string representing the table as a 2D array,
            prefixed with a system-info tag.
    """
    json_str = json.dumps(table_data, ensure_ascii=False)
    return (
        "<system-info>A table loaded as a JSON array:</system-info>\n"
        + json_str
    )


def _table_to_markdown(table_data: list[list[str]]) -> str:
    """Convert table data to Markdown format.

    Args:
        table_data (`list[list[str]]`):
            Table data represented as a 2D list.

    Returns:
        `str`:
            Table in Markdown format.
    """
    if not table_data:
        return ""

    num_cols = len(table_data[0]) if table_data else 0
    if num_cols == 0:
        return ""

    md_table = ""

    # Header row
    header_row = "| " + " | ".join(table_data[0]) + " |\n"
    md_table += header_row

    # Separator row
    separator_row = "| " + " | ".join(["---"] * num_cols) + " |\n"
    md_table += separator_row

    # Data rows
    for row in table_data[1:]:
        # Ensure row has same number of columns as header
        while len(row) < num_cols:
            row.append("")
        data_row = "| " + " | ".join(row[:num_cols]) + " |\n"
        md_table += data_row

    return md_table
