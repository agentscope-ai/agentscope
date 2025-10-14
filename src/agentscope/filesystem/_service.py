# -*- coding: utf-8 -*-
"""Filesystem domain service built atop a single FsHandle (联合授权).

The service enforces domain policies based on logical path prefixes and
delegates I/O to the underlying FsHandle. Tools should depend on this service
instead of capturing FsHandle/FileSystemBase directly.
"""
from __future__ import annotations

import difflib
import fnmatch
from dataclasses import dataclass
from typing import Iterable, List, Tuple

from ._handle import FsHandle, validate_path
from ._errors import AccessDeniedError, InvalidArgumentError, NotFoundError
from ._types import EntryMeta


USERINPUT_PREFIX = "/userinput/"
WORKSPACE_PREFIX = "/workspace/"
INTERNAL_PREFIX = "/internal/"


@dataclass
class DomainPolicy:
    """High-level behavioral switches per domain."""

    allow_write_userinput: bool = False
    allow_edit_userinput: bool = False

    allow_delete_internal: bool = False
    internal_default_summary: bool = True  # for future extension

    allow_delete_workspace: bool = True

    max_lines: int | None = 5000
    max_bytes: int | None = 1_000_000


class FileDomainService:
    """Service that routes operations by logical prefix and applies policy."""

    def __init__(
        self,
        handle: FsHandle,
        *,
        policy: DomainPolicy | None = None,
        allowed_roots: Iterable[str] | None = None,
    ) -> None:
        self._handle = handle
        self._policy = policy or DomainPolicy()
        self._allowed_roots = list(allowed_roots or [USERINPUT_PREFIX, WORKSPACE_PREFIX])

    # ------------------------------- helpers -------------------------------
    def _domain_of(self, path: str) -> str:
        path = validate_path(path)
        if path.startswith(USERINPUT_PREFIX):
            return USERINPUT_PREFIX
        if path.startswith(WORKSPACE_PREFIX):
            return WORKSPACE_PREFIX
        if path.startswith(INTERNAL_PREFIX):
            return INTERNAL_PREFIX
        # Treat unknown as not found in current view
        raise NotFoundError(path)

    def _enforce_read_limits(self, text: str) -> str:
        if self._policy.max_bytes is not None and len(text.encode("utf-8")) > self._policy.max_bytes:
            truncated = text.encode("utf-8")[: self._policy.max_bytes].decode("utf-8", errors="ignore")
            return truncated + "\n[truncated due to max_bytes limit]"
        if self._policy.max_lines is not None:
            lines = text.splitlines()
            if len(lines) > self._policy.max_lines:
                head = "\n".join(lines[: self._policy.max_lines])
                return head + "\n[truncated due to max_lines limit]"
        return text

    # ------------------------------- queries -------------------------------
    def list_directory(self, path: str) -> List[str]:
        prefix = path if path.endswith("/") else path + "/"
        prefix = validate_path(prefix)
        self._handle.list(prefix)  # permission check for list
        entries = self._handle.list(prefix)

        # Compute immediate children by stripping the prefix and taking first segment
        seen_dirs: set[str] = set()
        files: set[str] = set()
        for meta in entries:
            rel = meta["path"][len(prefix) :]
            if not rel:
                continue
            if "/" in rel:
                top = rel.split("/", 1)[0] + "/"
                seen_dirs.add(top)
            else:
                files.add(rel)
        out = [f"[DIR] {d}" for d in sorted(seen_dirs)] + [f"[FILE] {f}" for f in sorted(files)]
        return out

    def list_directory_with_sizes(self, path: str, sort_by: str = "name") -> Tuple[List[str], str]:
        lines = self._handle.list(path if path.endswith("/") else path + "/")
        # map: child -> (is_dir, size)
        prefix = path if path.endswith("/") else path + "/"
        children: dict[str, Tuple[bool, int]] = {}
        for meta in lines:
            rel = meta["path"][len(prefix) :]
            if not rel:
                continue
            if "/" in rel:
                top = rel.split("/", 1)[0] + "/"
                children.setdefault(top, (True, 0))
                # size aggregated below
            else:
                children[rel] = (False, int(meta.get("size", 0)))

        # aggregate dir sizes
        totals = {k: 0 for k, (is_dir, _) in children.items() if is_dir}
        for meta in lines:
            rel = meta["path"][len(prefix) :]
            if not rel or "/" not in rel:
                continue
            top = rel.split("/", 1)[0] + "/"
            totals[top] = totals.get(top, 0) + int(meta.get("size", 0))

        listing: List[Tuple[str, bool, int]] = []
        for name, (is_dir, size) in children.items():
            size_eff = totals[name] if is_dir else size
            listing.append((name, is_dir, size_eff))

        if sort_by == "size":
            listing.sort(key=lambda t: (t[1] is False, t[2], t[0]))  # dirs first, then size
        else:
            listing.sort(key=lambda t: (t[1] is False, t[0]))

        rendered = [
            f"[DIR] {n} (size={s})" if is_dir else f"[FILE] {n} (size={s})"
            for (n, is_dir, s) in listing
        ]
        summary = f"total: {len(listing)} entries; files={sum(1 for _, d, _ in listing if not d)}, dirs={sum(1 for _, d, _ in listing if d)}"
        return rendered, summary

    def search_files(self, path: str, pattern: str, exclude_patterns: Iterable[str] | None = None) -> List[str]:
        prefix = path if path.endswith("/") else path + "/"
        entries = self._handle.list(prefix)
        matches: List[str] = []
        excludes = list(exclude_patterns or [])
        for meta in entries:
            p = meta["path"]
            name = p.split("/")[-1]
            cond = fnmatch.fnmatch(name, pattern) or (pattern in name)
            if cond and not any(fnmatch.fnmatch(name, ex) for ex in excludes):
                matches.append(p)
        return sorted(matches)

    def get_file_info(self, path: str) -> EntryMeta:
        return self._handle.file(path)

    def list_allowed_directories(self) -> List[str]:
        return list(self._allowed_roots)

    # ------------------------------- reads ----------------------------------
    def read_text_file(self, path: str, start_line: int | None = 1, read_lines: int | None = None) -> str:
        if start_line is not None and start_line < 1:
            raise InvalidArgumentError("start_line", value=start_line)
        if read_lines is not None and read_lines <= 0:
            raise InvalidArgumentError("read_lines", value=read_lines)

        text = self._handle.read_file(path)
        lines = text.splitlines()
        s = (start_line or 1) - 1
        e = s + read_lines if read_lines is not None else len(lines)
        sliced = "\n".join(lines[s:e])
        return self._enforce_read_limits(sliced)

    def read_multiple_files(self, paths: Iterable[str]) -> List[dict]:
        out: List[dict] = []
        for p in paths:
            try:
                content = self.read_text_file(p)
                out.append({"path": p, "ok": True, "content": content})
            except Exception as e:  # per-item failure tolerated
                out.append({"path": p, "ok": False, "error": str(e)})
        return out

    # ------------------------------ mutations -------------------------------
    def _assert_writable(self, path: str) -> None:
        domain = self._domain_of(path)
        if domain == USERINPUT_PREFIX and not self._policy.allow_write_userinput:
            raise AccessDeniedError(path, "write")
        if domain == INTERNAL_PREFIX and not self._policy.allow_delete_internal:
            # write is allowed via grants; delete policy handled elsewhere
            pass

    def write_file(self, path: str, content: str) -> EntryMeta:
        self._assert_writable(path)
        return self._handle.write(path, content, overwrite=True)

    def edit_file(self, path: str, edits: List[dict], dry_run: bool = False) -> Tuple[str, int]:
        domain = self._domain_of(path)
        if domain == USERINPUT_PREFIX and not self._policy.allow_edit_userinput:
            raise AccessDeniedError(path, "write")
        if not edits:
            raise InvalidArgumentError("edits", value="empty")

        original = self._handle.read_file(path)
        new_text = original
        for e in edits:
            old = e.get("oldText", "")
            new = e.get("newText", "")
            if not isinstance(old, str) or not isinstance(new, str):
                raise InvalidArgumentError("edits", value=e)
            new_text = new_text.replace(old, new)

        if dry_run:
            diff = difflib.unified_diff(
                original.splitlines(),
                new_text.splitlines(),
                fromfile=path,
                tofile=path,
                lineterm="",
            )
            rendered = "\n".join(diff)
            return rendered or "(no changes)", 0

        meta = self._handle.write(path, new_text, overwrite=True)
        changed = 0 if original == new_text else 1
        return f"applied edits to {path}", changed

