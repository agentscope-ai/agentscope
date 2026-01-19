# -*- coding: utf-8 -*-
"""Filesystem domain service built atop a single FsHandle (联合授权).

The service enforces domain policies based on logical path prefixes and
delegates I/O to the underlying FsHandle. Tools should depend on this service
instead of capturing FsHandle/FileSystemBase directly.

Note: SOP limits atomic ops to
list/file/read_binary/read_file/read_re/write/delete.
This service does not provide 'dry-run' features.
"""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from typing import List, Tuple, Iterable

from ._handle import FsHandle, validate_path
from ._errors import AccessDeniedError, InvalidArgumentError, NotFoundError
from ._types import EntryMeta


USERINPUT_PREFIX = "/userinput/"
WORKSPACE_PREFIX = "/workspace/"
INTERNAL_PREFIX = "/internal/"


@dataclass
class DomainPolicy:
    """Behavioral switches per domain (minimal, no extras)."""

    allow_write_userinput: bool = False
    allow_delete_internal: bool = False


class FileDomainService:
    """Service that routes operations by logical prefix and applies policy."""

    def __init__(
        self,
        handle: FsHandle,
        *,
        policy: DomainPolicy | None = None,
    ) -> None:
        self._handle = handle
        self._policy = policy or DomainPolicy()

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

    # (no read limits; SOP/todo: read_text_file does pure slicing only)

    # ------------------------------- queries -------------------------------
    def list_directory(self, path: str) -> List[str]:
        prefix = path if path.endswith("/") else path + "/"
        prefix = validate_path(prefix)
        self._handle.list(prefix)  # permission check for list
        entries = self._handle.list(prefix)

        # Compute immediate children by stripping the prefix and taking the
        # first segment.
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
        out = [f"[DIR] {d}" for d in sorted(seen_dirs)] + [
            f"[FILE] {f}" for f in sorted(files)
        ]
        return out

    def list_directory_with_sizes(
        self,
        path: str,
        sort_by: str = "name",
    ) -> Tuple[List[str], str]:
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
            # dirs first, then size
            listing.sort(
                key=lambda t: (t[1] is False, t[2], t[0]),
            )
        else:
            listing.sort(key=lambda t: (t[1] is False, t[0]))

        rendered = [
            f"[DIR] {n} (size={s})" if is_dir else f"[FILE] {n} (size={s})"
            for (n, is_dir, s) in listing
        ]
        total_size = sum(int(m.get("size", 0)) for m in lines)
        summary = (
            f"total: {len(listing)} entries; "
            f"files={sum(1 for _, d, _ in listing if not d)}, "
            f"dirs={sum(1 for _, d, _ in listing if d)}; "
            f"total_size={total_size}"
        )
        return rendered, summary

    def search_files(
        self,
        path: str,
        pattern: str,
        exclude_patterns: list[str] | None = None,
    ) -> List[str]:
        prefix = path if path.endswith("/") else path + "/"
        entries = self._handle.list(prefix)
        matches: List[str] = []
        excludes = list(exclude_patterns or [])
        for meta in entries:
            p = meta["path"]
            cond = fnmatch.fnmatch(p, pattern) or (pattern in p)
            if cond and not any(fnmatch.fnmatch(p, ex) for ex in excludes):
                matches.append(p)
        return sorted(matches)

    def get_file_info(self, path: str) -> EntryMeta:
        return self._handle.file(path)

    def list_allowed_directories(self) -> List[str]:
        # Fixed set as per SOP/todo; no dynamic exposure of internal
        return [USERINPUT_PREFIX, WORKSPACE_PREFIX]

    # ------------------------------- reads ----------------------------------
    def read_text_file(
        self,
        path: str,
        start_line: int | None = 1,
        read_lines: int | None = None,
    ) -> str:
        if start_line is not None and start_line < 1:
            raise InvalidArgumentError("start_line", value=start_line)
        if read_lines is not None and read_lines <= 0:
            raise InvalidArgumentError("read_lines", value=read_lines)

        text = self._handle.read_file(path)
        lines = text.splitlines()
        s = (start_line or 1) - 1
        e = s + read_lines if read_lines is not None else len(lines)
        return "\n".join(lines[s:e])

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
        if (
            domain == USERINPUT_PREFIX
            and not self._policy.allow_write_userinput
        ):
            raise AccessDeniedError(path, "write")
        if (
            domain == INTERNAL_PREFIX
            and not self._policy.allow_delete_internal
        ):
            # write is allowed via grants; delete policy handled elsewhere
            pass

    def write_file(self, path: str, content: str) -> EntryMeta:
        self._assert_writable(path)
        return self._handle.write(path, content, overwrite=True)

    def edit_file(self, path: str, edits: list[dict]) -> EntryMeta:
        """Apply sequential textual edits and overwrite the file.

        Edits are ordered replacements with keys:
        {"oldText": str, "newText": str}.
        No dry-run or diff; exact substring replacement.
        """
        # treat as write operation on the target domain
        self._assert_writable(path)
        text = self._handle.read_file(path)
        for e in edits:
            old = e["oldText"]
            new = e["newText"]
            text = text.replace(old, new)
        return self._handle.write(path, text, overwrite=True)

    def delete_file(self, path: str) -> None:
        """Delete a file respecting domain policy.

        - `/userinput/`: always denied.
        - `/internal/`: denied unless policy explicitly allows deletion.
        - `/workspace/`: allowed when granted by handle.
        """
        domain = self._domain_of(path)
        if domain == USERINPUT_PREFIX:
            raise AccessDeniedError(path, "delete")
        if (
            domain == INTERNAL_PREFIX
            and not self._policy.allow_delete_internal
        ):
            raise AccessDeniedError(path, "delete")
        self._handle.delete(path)

    # ------------------------- diagnostics (optional) ------------------------
    def describe_permissions_markdown(self) -> str:
        """Return a human-readable markdown summary of this handle's grants.

        Intended for agent diagnostics or logging; not registered as a tool
        by default.
        """
        return self._handle.describe_grants_markdown()

    # ---------------------- tool enumeration (optional) ----------------------
    def tools(self) -> list[tuple[callable, "FileDomainService"]]:
        """Return the standard controlled filesystem tools bound to this
        service.

        This is backend-agnostic and mirrors the generic set in
        ``src/agentscope/filesystem/_tools.py``.
        """
        from . import _tools as T

        funcs = [
            T.read_text_file,
            T.read_multiple_files,
            T.list_directory,
            T.list_directory_with_sizes,
            T.search_files,
            T.get_file_info,
            T.list_allowed_directories,
            T.write_file,
            T.delete_file,
            T.edit_file,
            T.fs_describe_permissions_markdown,
        ]
        return [(f, self) for f in funcs]
