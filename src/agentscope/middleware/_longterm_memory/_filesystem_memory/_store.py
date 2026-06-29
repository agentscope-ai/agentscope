# -*- coding: utf-8 -*-
"""Persistent Markdown document store for filesystem-backed long-term memory.

``FileSystemMemoryStore`` owns the on-disk layout, document-size limits,
constrained mutation semantics, static-extraction metadata, lightweight
retrieval, and the small amount of backend file access needed by those
operations. It has no Agent or workspace lifecycle dependency; the middleware
supplies only a backend instance and the resolved workspace workdir.

The store favors predictable human-editable files over database-like
features. Updates reread the current document, exact replacements must be
unique, and additions target existing Markdown sections unless section
creation is explicitly requested.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TypeAlias

from .... import logger
from ....tool import BackendBase


DEFAULT_MEMORY = """# Long-term Memory

## Stable Facts

## Project Knowledge

## Decisions

## Reusable Lessons
"""

DEFAULT_USER = """# User Profile

## Identity And Background

## Preferences

## Communication Style

## Recurring Goals

## Constraints
"""


@dataclass(slots=True)
class MemoryPromptSnapshot:
    """The two bounded documents injected into an agent system prompt."""

    memory: str
    user: str


# Modification timestamps of ``MEMORY.md`` and ``USER.md``.
SnapshotVersion: TypeAlias = tuple[float, float]


@dataclass(slots=True)
class MemorySearchResult:
    """One matching Markdown section returned by lexical retrieval."""

    source: str
    section: str
    content: str
    relevance: float


@dataclass(slots=True)
class MemoryFileHeader:
    """Lightweight header for one memory file, read from frontmatter only."""

    filename: str
    """Relative path under the memory directory (e.g. ``user_role.md``)."""
    path: str
    """Absolute path inside the backend."""
    description: str | None
    """One-line description from frontmatter; ``None`` when absent."""
    type: str | None
    """Memory type tag from frontmatter (user/feedback/project/reference)."""
    mtime: float | None
    """Modification time as a Unix timestamp; ``None`` when unavailable."""


class FileSystemMemoryStore:
    """Own the constrained ``Memory/`` layout and document operations."""

    FILENAME_MEMORY_MD: str = "MEMORY.md"

    def __init__(
        self,
        backend: BackendBase,
        workdir: str,
        *,
        memory_dir: str,
        user_max_length: int,
        memory_max_length: int,
        daily_max_length: int,
    ) -> None:
        """Initialize paths and size limits for one workspace store.

        Args:
            backend:
                Backend used for every file operation.
            workdir:
                Workspace filesystem root below which every memory path is
                resolved.
            memory_dir:
                Workspace-relative root for all memory files.
            user_max_length:
                Maximum size of ``USER.md`` after an update.
            memory_max_length:
                Maximum size of ``MEMORY.md`` after an update.
            daily_max_length:
                Maximum size of any one dated daily-memory file.
        """
        self._backend = backend
        self._workdir = workdir

        self._memory_dir = memory_dir.strip("/\\")

        self._user_max_length = user_max_length
        self._memory_max_length = memory_max_length
        self._daily_max_length = daily_max_length

        # The cached content
        self._cached_user_md_content = None
        self._cached_user_md_mtime = None

        self._cached_memory_md_content = None
        self._cached_memory_md_mtime = None

    def get_memory_dir(self) -> str:
        """Get the memory directory"""
        return self._backend.join_path(self._workdir, self._memory_dir)

    def get_memory_md_path(self) -> str:
        """Get the MEMORY.md path."""
        return self._backend.join_path(
            self.get_memory_dir(),
            self.FILENAME_MEMORY_MD,
        )

    async def get_memory_md_content(self) -> str | None:
        """Get the content of the MEMORY.md file."""
        path = self.get_memory_md_path()
        mtime = await self._backend.stat_mtime(path)
        if self._cached_memory_md_content is None or (
            self._cached_memory_md_mtime is not None
            and mtime is not None
            and mtime > self._cached_memory_md_content
        ):
            if self._backend.file_exists(path):
                content = (await self._backend.read_file(path)).decode("utf-8")
                self._cached_memory_md_content = content
                self._cached_memory_md_mtime = mtime
                return content
            return None
        return self._cached_memory_md_content

    async def ensure_layout(self) -> None:
        """Create the memory directory and initial files idempotently.

        Existing human-edited documents are never replaced. Metadata is also
        created only when absent, allowing turn counters to survive restarts.
        """
        if not self._backend.is_dir(self.get_memory_dir()):
            await self._backend.exec_shell(
                command=["mkdir", "-p", self.get_memory_dir()],
            )

        if not await self._backend.file_exists(self.get_memory_md_path()):
            logger.info(
                f"Creating 'MEMORY.md' file in '{self._workdir}'",
            )
            await self._backend.write_file(
                self.get_memory_md_path(),
                "".encode("utf-8"),
            )

    # ------------------------------------------------------------------
    # Frontmatter helpers
    # ------------------------------------------------------------------

    _FRONTMATTER_RE = re.compile(
        r"^\s*---\s*\n(?P<body>.*?)\n---\s*\n",
        re.DOTALL,
    )
    _FIELD_RE = re.compile(r"^(?P<key>\w+)\s*:\s*(?P<value>.+)$", re.MULTILINE)

    @classmethod
    def _parse_frontmatter_fields(cls, content: str) -> dict[str, str]:
        """Return a dict of YAML-like key/value pairs from the first
        frontmatter block.  Only scalar ``key: value`` lines are parsed;
        nested structures are intentionally ignored."""
        m = cls._FRONTMATTER_RE.match(content)
        if not m:
            return {}
        return {
            fm.group("key"): fm.group("value").strip()
            for fm in cls._FIELD_RE.finditer(m.group("body"))
        }

    # ------------------------------------------------------------------
    # File listing
    # ------------------------------------------------------------------

    _MAX_MEMORY_FILES = 200
    _FRONTMATTER_MAX_BYTES = 1_024  # read at most 1 KB to find frontmatter

    async def list_md_files(self) -> list[MemoryFileHeader]:
        """Scan the memory directory for individual memory files.

        Returns a list of :class:`MemoryFileHeader` objects sorted
        newest-first and capped at ``_MAX_MEMORY_FILES``.  The two
        system files (``MEMORY.md`` and ``USER.md``) are excluded so
        that only agent-written topic files are returned.
        """
        memory_dir = self.get_memory_dir()
        system_files = {self.FILENAME_MEMORY_MD, self.FILENAME_USER_MD}

        try:
            all_entries = await self._backend.list_dir(
                memory_dir,
                recursive=True,
            )
        except Exception:
            return []

        md_entries = [
            e
            for e in all_entries
            if e.endswith(".md") and e not in system_files
        ]

        headers: list[MemoryFileHeader] = []
        for relative in md_entries:
            full_path = self._backend.join_path(memory_dir, relative)
            try:
                raw = await self._backend.read_file(full_path)
                # Only parse the leading bytes to keep this cheap.
                snippet = raw[: self._FRONTMATTER_MAX_BYTES].decode(
                    "utf-8",
                    errors="replace",
                )
                fields = self._parse_frontmatter_fields(snippet)
                mtime = await self._backend.stat_mtime(full_path)
                headers.append(
                    MemoryFileHeader(
                        filename=relative,
                        path=full_path,
                        description=fields.get("description") or None,
                        type=fields.get("type") or None,
                        mtime=mtime,
                    ),
                )
            except Exception:
                continue

        headers.sort(key=lambda h: h.mtime or 0.0, reverse=True)
        return headers[: self._MAX_MEMORY_FILES]
