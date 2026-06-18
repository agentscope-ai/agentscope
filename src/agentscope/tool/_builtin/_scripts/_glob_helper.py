#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Standalone glob helper script for agentscope builtin tools.

This script is designed to run **without** agentscope installed. It is
deployed into remote workspaces (Docker / E2B) at initialization time
and invoked via ``exec_shell`` by the :class:`Glob` tool.

Usage::

    python3 _glob_helper.py --pattern '**/*.py' --base-dir /workspace

Output: a JSON array of matching file paths, sorted by modification
time (newest first).  Exits with code 0 on success (even when no
matches are found — the array is simply empty).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

# ── glob matching (mirrors the logic from Glob tool) ──────────────


def _glob_part_to_regex(part: str) -> re.Pattern[str]:
    """Convert a single glob pattern segment to a compiled regex.

    Translates glob wildcards (``*`` → ``.*``, ``?`` → ``.``) and
    escapes regex meta-characters so that a segment like ``*.py``
    becomes the anchored pattern ``^.*\\.py$``.

    Args:
        part: One path segment of a glob pattern (e.g. ``*.py``
            or ``test_*``).

    Returns:
        A compiled :class:`re.Pattern` anchored with ``^…$``.
    """
    regex_str = ""
    for c in part:
        if c == "*":
            regex_str += ".*"
        elif c == "?":
            regex_str += "."
        elif c in ".^$+{}[]|()\\":
            regex_str += "\\" + c
        else:
            regex_str += c
    return re.compile(f"^{regex_str}$")


def _collect_all(current_dir: str, results: list[str]) -> None:
    """Recursively collect all file paths under *current_dir*.

    Uses :func:`os.walk` to traverse the directory tree.
    ``PermissionError`` and ``OSError`` are silently ignored so that
    inaccessible subtrees do not abort the entire glob operation.

    Args:
        current_dir: Root directory to walk.
        results: Accumulator list; matched file paths are appended
            in-place.
    """
    try:
        for root, _dirs, files in os.walk(current_dir):
            for fname in files:
                results.append(os.path.join(root, fname))
    except (PermissionError, OSError):
        pass


def _match_parts(
    parts: list[str],
    part_index: int,
    current_dir: str,
    results: list[str],
) -> None:
    """Recursively match glob pattern *parts* against directory entries.

    Walks the filesystem starting from *current_dir*, consuming one
    pattern segment per directory level.  The ``**`` segment is handled
    specially: it matches zero or more intermediate directories by
    recursing into every subdirectory while keeping the same
    *part_index*, and also advancing to the next segment in the
    current directory.

    Args:
        parts: The glob pattern split into path segments
            (e.g. ``["src", "**", "*.py"]``).
        part_index: Index into *parts* indicating which segment is
            being matched at this recursion level.
        current_dir: The directory currently being scanned.
        results: Accumulator list; matched file paths are appended
            in-place.
    """
    if part_index >= len(parts):
        return

    part = parts[part_index]
    is_last = part_index == len(parts) - 1

    if part == "**":
        if is_last:
            _collect_all(current_dir, results)
        else:
            _match_parts(parts, part_index + 1, current_dir, results)
            try:
                with os.scandir(current_dir) as entries:
                    for entry in entries:
                        if entry.is_dir(follow_symlinks=False):
                            _match_parts(
                                parts,
                                part_index,
                                entry.path,
                                results,
                            )
            except (PermissionError, OSError):
                pass
    else:
        regex = _glob_part_to_regex(part)
        try:
            with os.scandir(current_dir) as entries:
                for entry in entries:
                    if regex.match(entry.name):
                        full_path = entry.path
                        if is_last:
                            if entry.is_file(follow_symlinks=False):
                                results.append(full_path)
                        elif entry.is_dir(follow_symlinks=False):
                            _match_parts(
                                parts,
                                part_index + 1,
                                full_path,
                                results,
                            )
        except (PermissionError, OSError):
            pass


def glob_match(pattern: str, base_dir: str) -> list[str]:
    """Match files against a glob pattern starting from *base_dir*.

    Splits *pattern* on path separators (``/`` or ``\\``) and
    delegates to :func:`_match_parts` for recursive directory
    traversal.  Supports ``*`` (any characters within a segment),
    ``?`` (single character), and ``**`` (zero or more directories).

    Args:
        pattern: Glob pattern such as ``"**/*.py"`` or
            ``"src/utils/*.txt"``.
        base_dir: Absolute path of the directory to search from.

    Returns:
        A list of absolute file paths that match *pattern*.  The
        list is unsorted; callers should sort as needed (e.g. by
        modification time).
    """
    results: list[str] = []
    parts = [p for p in re.split(r"[\\/]+", pattern) if p]
    _match_parts(parts, 0, base_dir, results)
    return results


# ── entry point ───────────────────────────────────────────────────


def main() -> None:
    """CLI entry point: parse ``--pattern`` and ``--base-dir``, run
    the glob, and print results as a JSON array to stdout.

    The results are sorted by file modification time (newest first).
    If *base_dir* does not exist, an empty JSON array ``[]`` is
    printed and the process exits with code 0.
    """
    parser = argparse.ArgumentParser(
        description="Glob file matching with mtime sorting.",
    )
    parser.add_argument(
        "--pattern",
        required=True,
        help="Glob pattern (e.g. '**/*.py')",
    )
    parser.add_argument(
        "--base-dir",
        required=True,
        help="Base directory to search from",
    )
    args = parser.parse_args()

    if not os.path.isdir(args.base_dir):
        # Empty result for non-existent directory (caller handles
        # the "directory not found" error message).
        json.dump([], sys.stdout)
        return

    matches = glob_match(args.pattern, args.base_dir)

    # Sort by modification time, newest first.
    def _mtime(path: str) -> float:
        try:
            return os.stat(path).st_mtime
        except (OSError, FileNotFoundError):
            return 0.0

    matches.sort(key=_mtime, reverse=True)

    json.dump(matches, sys.stdout)


if __name__ == "__main__":
    main()
