# -*- coding: utf-8 -*-
"""Backend-specific tests for the in-memory filesystem."""
import pytest

from agentscope.filesystem import (
    ConflictError,
    InMemoryFileSystem,
    NotFoundError,
)

ALL_OPS = {
    "list",
    "file",
    "read_binary",
    "read_file",
    "read_re",
    "write",
    "delete",
}


def test_write_conflict_and_delete() -> None:
    fs = InMemoryFileSystem()
    handle = fs.create_handle([
        {"prefix": "/workspace/", "ops": set(ALL_OPS)},
    ])

    handle.write("/workspace/result.txt", "first")
    with pytest.raises(ConflictError):
        handle.write("/workspace/result.txt", "second", overwrite=False)

    handle.delete("/workspace/result.txt")
    with pytest.raises(NotFoundError):
        handle.read_file("/workspace/result.txt")


def test_line_and_regex_utilities() -> None:
    fs = InMemoryFileSystem()
    handle = fs.create_handle([
        {"prefix": "/workspace/", "ops": set(ALL_OPS)},
    ])

    payload = "line0\nline1\nline2\nline3"
    handle.write("/workspace/log.txt", payload)

    assert handle.read_file("/workspace/log.txt", index=1, line=2) == "line1\nline2"
    assert handle.read_binary("/workspace/log.txt") == payload.encode("utf-8")

    matches = handle.read_re("/workspace/log.txt", r"line[0-3]", overlap=1)
    assert matches[:2] == ["line0", "line1"]
