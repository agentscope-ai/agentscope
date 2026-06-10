# -*- coding: utf-8 -*-
"""Result models for filesystem operations."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class FileInfo:
    """Metadata about a file or directory."""

    name: str
    is_directory: bool = False
    size_bytes: int = 0
    modified_at: float = 0.0


@dataclass
class LsResult:
    """Result of listing a directory."""

    path: str
    entries: list[FileInfo] = field(default_factory=list)


@dataclass
class ReadResult:
    """Result of reading a file."""

    path: str
    content: str = ""
    is_binary: bool = False
    offset: int = 0
    total_lines: int = 0


@dataclass
class WriteResult:
    """Result of a write operation."""

    path: str
    bytes_written: int = 0


@dataclass
class EditResult:
    """Result of an edit operation."""

    path: str
    replacements: int = 0
    original: str = ""
    modified: str = ""


@dataclass
class GrepMatch:
    """A single grep match."""

    path: str
    line_number: int
    line_text: str


@dataclass
class GrepResult:
    """Result of a grep search."""

    pattern: str
    matches: list[GrepMatch] = field(default_factory=list)


@dataclass
class GlobResult:
    """Result of a glob search."""

    pattern: str
    paths: list[str] = field(default_factory=list)


@dataclass
class FileUploadResponse:
    """Result of a file upload operation."""

    path: str
    bytes_written: int = 0
    success: bool = True


@dataclass
class FileDownloadResponse:
    """Result of a file download operation."""

    path: str
    content: bytes = b""
    success: bool = True
