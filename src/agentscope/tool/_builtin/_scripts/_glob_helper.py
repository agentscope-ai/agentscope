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
    """Convert a single glob pattern segment to a compiled regex."""
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
    """Recursively collect all files under *current_dir*."""
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
    """Recursively match glob pattern *parts* against directory entries."""
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

    Returns a list of absolute file paths.
    """
    results: list[str] = []
    parts = [p for p in re.split(r"[\\/]+", pattern) if p]
    _match_parts(parts, 0, base_dir, results)
    return results


# ── entry point ───────────────────────────────────────────────────


def main() -> None:
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
