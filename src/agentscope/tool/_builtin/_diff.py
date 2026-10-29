# -*- coding: utf-8 -*-
"""Unified diff formatting for builtin file tools."""
import difflib


def _unified_diff(
    original: str,
    updated: str,
    fromfile: str,
    tofile: str,
) -> str:
    """Build a diff with explicit records for unterminated file lines.

    Args:
        original (`str`): Original file content.
        updated (`str`): Updated file content.
        fromfile (`str`): Original file label.
        tofile (`str`): Updated file label.

    Returns:
        `str`: Unified diff, including missing-final-newline markers.
    """
    records = []
    for record in difflib.unified_diff(
        original.splitlines(keepends=True),
        updated.splitlines(keepends=True),
        fromfile=fromfile,
        tofile=tofile,
        n=3,
    ):
        records.append(record)
        if not record.endswith("\n"):
            # difflib leaves these records unterminated; joining them
            # directly would concatenate deletion and insertion records.
            records.append("\n\\ No newline at end of file\n")
    return "".join(records)
