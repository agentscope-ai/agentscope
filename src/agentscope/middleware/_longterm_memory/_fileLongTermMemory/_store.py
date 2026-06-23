# -*- coding: utf-8 -*-
"""Persistent Markdown document store for file-backed long-term memory.

``FileLTMStore`` owns the on-disk layout, document-size limits, constrained
mutation semantics, static-extraction metadata, and lightweight retrieval.
It has no Agent or workspace lifecycle dependency; all I/O is delegated to a
``WorkspaceFileAccessor`` supplied by the middleware.

The store favors predictable human-editable files over database-like
features. Updates reread the current document, exact replacements must be
unique, and additions target existing Markdown sections unless section
creation is explicitly requested.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal

from ._accessor import WorkspaceFileAccessor


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
class LTMSnapshot:
    """The two bounded documents injected into an agent system prompt."""

    memory: str
    user: str


@dataclass(slots=True)
class MemorySearchResult:
    """One matching Markdown section returned by lexical retrieval."""

    source: str
    section: str
    content: str
    relevance: float


class FileLTMStore:
    """Own the constrained ``Memory/`` layout and document operations."""

    def __init__(
        self,
        accessor: WorkspaceFileAccessor,
        *,
        memory_dir: str = "Memory",
        user_max_chars: int = 2_000,
        memory_max_chars: int = 4_000,
        daily_max_chars: int = 8_000,
    ) -> None:
        """Initialize paths and size limits for one workspace store.

        Args:
            accessor:
                UTF-8 file access rooted in the owning workspace.
            memory_dir:
                Workspace-relative root for all LTM files.
            user_max_chars:
                Maximum size of ``USER.md`` after an update.
            memory_max_chars:
                Maximum size of ``MEMORY.md`` after an update.
            daily_max_chars:
                Maximum size of any one dated daily-memory file.
        """
        self._accessor = accessor
        self.memory_dir = memory_dir.strip("/\\")
        self.daily_dir = f"{self.memory_dir}/memory"
        self.memory_path = f"{self.memory_dir}/MEMORY.md"
        self.user_path = f"{self.memory_dir}/USER.md"
        self.meta_path = f"{self.memory_dir}/.ltm.meta.json"
        self.user_max_chars = user_max_chars
        self.memory_max_chars = memory_max_chars
        self.daily_max_chars = daily_max_chars

    async def ensure_layout(self) -> None:
        """Create the LTM directory and initial files idempotently.

        Existing human-edited documents are never replaced. Metadata is also
        created only when absent, allowing turn counters to survive restarts.
        """
        await self._accessor.ensure_dir(self.daily_dir)
        meta = {
            "version": 1,
            "turn_count": 0,
            "last_static_update_turn": 0,
            "last_updated_at": None,
        }
        for path, content in (
            (self.memory_path, DEFAULT_MEMORY),
            (self.user_path, DEFAULT_USER),
            (self.meta_path, json.dumps(meta, indent=2) + "\n"),
        ):
            if not await self._accessor.exists(path):
                await self._accessor.write_text(path, content)

    async def read_snapshot(self) -> LTMSnapshot:
        """Read the complete USER and MEMORY prompt snapshots."""
        await self.ensure_layout()
        return LTMSnapshot(
            memory=await self._accessor.read_text(self.memory_path),
            user=await self._accessor.read_text(self.user_path),
        )

    async def read_target(
        self,
        target: Literal["user", "memory", "daily"],
        *,
        daily_date: str | None = None,
    ) -> str:
        """Read one logical memory target, returning an empty missing daily."""
        content, _ = await self.read_target_with_sections(
            target,
            daily_date=daily_date,
        )
        return content

    async def read_target_with_sections(
        self,
        target: Literal["user", "memory", "daily"],
        *,
        daily_date: str | None = None,
    ) -> tuple[str, list[str]]:
        """Read a target and return its level-two section headings.

        This combined operation avoids reading the same backend file twice for
        ``memory_read`` output.
        """
        await self.ensure_layout()
        path = self._target_path(target, daily_date)
        if not await self._accessor.exists(path):
            return "", []
        content = await self._accessor.read_text(path)
        return content, self._section_headings(content)

    async def update_target(
        self,
        *,
        action: Literal["add", "replace", "remove"],
        target: Literal["user", "memory", "daily"],
        content: str | None = None,
        old_text: str | None = None,
        section: str | None = None,
        create_section: bool = False,
        daily_date: str | None = None,
    ) -> str:
        """Apply one constrained mutation and return its workspace path.

        ``add`` deduplicates normalized content and can place a bullet inside
        an existing section. Creating a new section in any target requires the
        explicit ``create_section`` flag.
        ``replace`` and ``remove`` require ``old_text`` to match exactly once.

        The caller serializes writes with a workspace lock; this method still
        rereads the document immediately before mutation so it never operates
        on a prompt snapshot cached earlier in the turn.
        """
        await self.ensure_layout()
        path = self._target_path(target, daily_date)
        current = (
            await self._accessor.read_text(path)
            if await self._accessor.exists(path)
            else self._daily_header(daily_date)
        )

        if create_section:
            if action != "add":
                raise ValueError(
                    "`create_section` is only valid for action=add.",
                )
            if not section:
                raise ValueError(
                    "`section` is required when create_section is true.",
                )
        if action == "add":
            if not content or not content.strip():
                raise ValueError("`content` is required for add.")
            addition = content.strip()
            if self._contains_normalized(current, addition):
                return path
            if section:
                updated = self._add_to_section(
                    current,
                    section=section,
                    addition=addition,
                    create_section=create_section,
                )
            else:
                updated = f"{current.rstrip()}\n\n- {addition}\n"
        elif action in ("replace", "remove"):
            if not old_text:
                raise ValueError(f"`old_text` is required for {action}.")
            if action == "replace" and content is None:
                raise ValueError("`content` is required for replace.")
            matches = current.count(old_text)
            if matches != 1:
                raise ValueError(
                    f"`old_text` must match exactly once, found {matches}.",
                )
            replacement = "" if action == "remove" else (content or "")
            updated = current.replace(old_text, replacement, 1)
        else:
            raise ValueError(f"Unsupported memory action: {action}")

        self._check_limit(target, updated)
        await self._accessor.write_text(path, updated)
        return path

    async def apply_edits(
        self,
        target: Literal["user", "memory", "daily"],
        *,
        add: list[tuple[str, str, bool]],
        replace: list[tuple[str, str]],
        remove: list[str],
        daily_date: str | None = None,
    ) -> None:
        """Apply structured extractor edits to one memory document.

        A model may produce a replacement based on a snapshot that became
        stale before the lock was acquired. Invalid replacements/removals are
        therefore ignored instead of aborting all other extracted edits.
        ``daily_date=None`` targets today's notebook.
        """
        for old_text in remove:
            try:
                await self.update_target(
                    action="remove",
                    target=target,
                    old_text=old_text,
                    daily_date=daily_date,
                )
            except ValueError:
                continue
        for old_text, content in replace:
            try:
                await self.update_target(
                    action="replace",
                    target=target,
                    old_text=old_text,
                    content=content,
                    daily_date=daily_date,
                )
            except ValueError:
                continue
        for section, content, create_section in add:
            await self.update_target(
                action="add",
                target=target,
                content=content,
                section=section,
                create_section=create_section,
                daily_date=daily_date,
            )

    async def increment_turn(self) -> tuple[int, int]:
        """Increment and persist the completed-turn counter."""
        await self.ensure_layout()
        meta = await self._read_meta()
        meta["turn_count"] = int(meta.get("turn_count", 0)) + 1
        await self._write_meta(meta)
        return meta["turn_count"], int(meta.get("last_static_update_turn", 0))

    async def get_turn_state(self) -> tuple[int, int]:
        """Return completed turns and the last static extraction turn."""
        await self.ensure_layout()
        meta = await self._read_meta()
        return (
            int(meta.get("turn_count", 0)),
            int(meta.get("last_static_update_turn", 0)),
        )

    async def mark_static_update(self, turn_count: int) -> None:
        """Record the turn covered by the latest static extraction."""
        meta = await self._read_meta()
        meta["last_static_update_turn"] = turn_count
        meta["last_updated_at"] = datetime.now().astimezone().isoformat()
        await self._write_meta(meta)

    async def search(
        self,
        query: str,
        *,
        scope: Literal["daily", "all"] = "daily",
        days: int = 30,
        limit: int = 5,
        today: date | None = None,
    ) -> list[MemorySearchResult]:
        """Search Markdown sections with simple phrase/token matching.

        Daily filenames outside the requested date window are skipped before
        file reads. Exact normalized phrase matches rank above token matches;
        no embedding model, recency weight, or reranker is involved.
        """
        await self.ensure_layout()
        query = query.strip()
        if not query or limit <= 0:
            if not query:
                content = "no query provided."
            else:
                content = "limit is less than 1."
            res = MemorySearchResult(
                source="Error: Invalid input.",
                section="Invalid input.",
                content="Search failed, " + content,
                relevance=0.0,
            )
            return [res]

        current_date = today or datetime.now().astimezone().date()
        earliest = current_date - timedelta(days=max(days - 1, 0))
        # Discover only well-formed dated files in the requested lookback
        # window. The first tuple value is user-facing; the second is the
        # workspace-relative backend path.
        files: list[tuple[str, str]] = []
        for filename in await self._accessor.list_files(self.daily_dir, ".md"):
            try:
                file_date = date.fromisoformat(filename.removesuffix(".md"))
            except ValueError:
                continue
            if earliest <= file_date <= current_date:
                files.append(
                    (f"memory/{filename}", f"{self.daily_dir}/{filename}"),
                )
        if scope == "all":
            files.extend(
                [("MEMORY.md", self.memory_path), ("USER.md", self.user_path)],
            )

        # English-like text is tokenized into words; contiguous CJK runs are
        # expanded into character bigrams by _search_terms.
        terms = self._search_terms(query)
        normalized_query = self._normalize(query)
        ranked: list[tuple[tuple[int, int, str], MemorySearchResult]] = []
        for source, path in files:
            if not await self._accessor.exists(path):
                continue
            text = await self._accessor.read_text(path)
            for section, chunk in self._markdown_chunks(text):
                normalized = self._normalize(chunk)
                phrase = int(normalized_query in normalized)
                matched = sum(1 for term in terms if term in normalized)
                if not phrase and matched == 0:
                    continue
                relevance = 1.0 if phrase else matched / max(len(terms), 1)
                result = MemorySearchResult(
                    source=source,
                    section=section,
                    content=chunk.strip(),
                    relevance=round(relevance, 3),
                )
                ranked.append(((phrase, matched, source), result))
        # Phrase hit, number of matched terms, and source name form a small,
        # deterministic ranking key suitable for this intentionally light LTM.
        ranked.sort(key=lambda item: item[0], reverse=True)
        return [item[1] for item in ranked[:limit]]

    def _target_path(self, target: str, daily_date: str | None) -> str:
        """Map a logical target to a workspace-relative file path."""
        if target == "user":
            return self.user_path
        if target == "memory":
            return self.memory_path
        if target == "daily":
            value = (
                daily_date or datetime.now().astimezone().date().isoformat()
            )
            date.fromisoformat(value)
            return f"{self.daily_dir}/{value}.md"
        raise ValueError(f"Unknown memory target: {target}")

    @staticmethod
    def _daily_header(value: str | None) -> str:
        """Build the level-one heading for a dated daily-memory file."""
        day = value or datetime.now().astimezone().date().isoformat()
        return f"# {day}\n"

    def _check_limit(self, target: str, content: str) -> None:
        """Reject a mutation that would exceed its document character cap."""
        limit = {
            "user": self.user_max_chars,
            "memory": self.memory_max_chars,
            "daily": self.daily_max_chars,
        }[target]
        if len(content) > limit:
            raise ValueError(
                f"{target} memory would exceed its {limit}-character limit "
                f"(current: {len(content)}-character). "
                f"Please consolidate or merge existing entries before retrying, "
                f"and discard less important memories if necessary."
            )

    async def _read_meta(self) -> dict:
        """Read extraction metadata, recovering safely from invalid JSON."""
        try:
            return json.loads(await self._accessor.read_text(self.meta_path))
        except (json.JSONDecodeError, OSError):
            return {
                "version": 1,
                "turn_count": 0,
                "last_static_update_turn": 0,
                "last_updated_at": None,
            }

    async def _write_meta(self, meta: dict) -> None:
        """Persist extraction metadata as readable UTF-8 JSON."""
        await self._accessor.write_text(
            self.meta_path,
            json.dumps(meta, indent=2, ensure_ascii=False) + "\n",
        )

    @staticmethod
    def _normalize(text: str) -> str:
        """Normalize case and whitespace for deduplication and search."""
        return " ".join(text.casefold().split())

    @classmethod
    def _contains_normalized(cls, text: str, candidate: str) -> bool:
        """Return whether normalized ``candidate`` is already in ``text``."""
        return cls._normalize(candidate) in cls._normalize(text)

    @classmethod
    def _add_to_section(
        cls,
        text: str,
        *,
        section: str,
        addition: str,
        create_section: bool = False,
    ) -> str:
        """Append a bullet to a level-two section, optionally creating it.

        Heading matching is case/whitespace normalized, but duplicate matching
        headings are rejected because choosing one would be ambiguous.
        """
        section = cls._validate_section_name(section)
        headings = list(
            re.finditer(r"(?m)^##[ \t]+([^\r\n]+?)[ \t]*$", text),
        )
        normalized_section = cls._normalize(section)
        matching_indices = [
            index
            for index, match in enumerate(headings)
            if cls._normalize(match.group(1)) == normalized_section
        ]
        if not matching_indices:
            if create_section:
                return f"{text.rstrip()}\n\n## {section}\n\n- {addition}\n"
            available = ", ".join(
                match.group(1).strip() for match in headings
            )
            available_text = available or "(none)"
            raise ValueError(
                f"Unknown section {section!r}. Available sections: "
                f"{available_text}.",
            )
        if len(matching_indices) > 1:
            raise ValueError(
                f"Section {section!r} must occur exactly once, found "
                f"{len(matching_indices)}.",
            )

        match_index = matching_indices[0]
        end = (
            headings[match_index + 1].start()
            if match_index + 1 < len(headings)
            else len(text)
        )
        before = text[:end].rstrip()
        after = text[end:].lstrip("\r\n")
        if after:
            return f"{before}\n\n- {addition}\n\n{after}"
        return f"{before}\n\n- {addition}\n"

    @staticmethod
    def _validate_section_name(section: str) -> str:
        """Validate a safe plain-text level-two heading name."""
        value = section.strip()
        if not value or len(value) > 100:
            raise ValueError("`section` must contain 1-100 characters.")
        if "\n" in value or "\r" in value or "#" in value:
            raise ValueError(
                "`section` must be a plain heading without newlines or '#'.",
            )
        return value

    @staticmethod
    def _section_headings(text: str) -> list[str]:
        """Extract level-two heading names in document order."""
        return [
            match.group(1).strip()
            for match in re.finditer(
                r"(?m)^##[ \t]+([^\r\n]+?)[ \t]*$",
                text,
            )
        ]

    @staticmethod
    def _search_terms(query: str) -> set[str]:
        """Build lightweight word and CJK-bigram search terms."""
        lowered = query.casefold()
        words = set(re.findall(r"[a-z0-9_]+", lowered))
        for run in re.findall(r"[\u4e00-\u9fff]+", lowered):
            if len(run) == 1:
                words.add(run)
            else:
                words.update(
                    run[index : index + 2] for index in range(len(run) - 1)
                )
        return words

    @staticmethod
    def _markdown_chunks(text: str) -> list[tuple[str, str]]:
        """Split a document into level-two sections for retrieval."""
        chunks: list[tuple[str, str]] = []
        heading = "Document"
        buffer: list[str] = []
        for line in text.splitlines():
            if line.startswith("## "):
                if buffer and any(item.strip() for item in buffer):
                    chunks.append((heading, "\n".join(buffer)))
                heading = line.removeprefix("## ").strip()
                buffer = [line]
            else:
                buffer.append(line)
        if buffer and any(item.strip() for item in buffer):
            chunks.append((heading, "\n".join(buffer)))
        return chunks
