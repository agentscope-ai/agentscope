# -*- coding: utf-8 -*-
"""Permission enforcement tests for the logical filesystem handle."""
import pytest

from agentscope.filesystem import (
    AccessDeniedError,
    InMemoryFileSystem,
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


def _workspace_grant(ops: set[str]) -> dict:
    return {"prefix": "/workspace/", "ops": ops}


def test_read_only_grant_blocks_write() -> None:
    fs = InMemoryFileSystem()
    admin = fs.create_handle([_workspace_grant(set(ALL_OPS))])
    admin.write("/workspace/data.txt", "payload")

    reader = fs.create_handle([
        _workspace_grant({"list", "file", "read_file", "read_binary"}),
    ])
    assert reader.file("/workspace/data.txt")["path"] == "/workspace/data.txt"
    assert reader.read_file("/workspace/data.txt") == "payload"

    with pytest.raises(AccessDeniedError):
        reader.write("/workspace/data.txt", "new")

    with pytest.raises(AccessDeniedError):
        reader.delete("/workspace/data.txt")


def test_prefix_is_enforced() -> None:
    fs = InMemoryFileSystem()
    admin = fs.create_handle([_workspace_grant(set(ALL_OPS))])
    admin.write("/workspace/a.txt", "content")

    handle = fs.create_handle([
        {"prefix": "/userinput/", "ops": {"list", "file", "read_file"}},
    ])

    with pytest.raises(AccessDeniedError):
        handle.read_file("/workspace/a.txt")


def test_new_handle_sees_latest_state() -> None:
    fs = InMemoryFileSystem()
    admin = fs.create_handle([_workspace_grant(set(ALL_OPS))])
    admin.write("/workspace/item.txt", "data")

    reader = fs.create_handle([
        _workspace_grant({"list", "file", "read_file"}),
    ])
    assert any(entry["path"].endswith("item.txt") for entry in reader.list())
    admin.delete("/workspace/item.txt")

    # New handle should observe deletion immediately via snapshot refresh.
    reader_after = fs.create_handle([
        _workspace_grant({"list", "file", "read_file"}),
    ])
    assert reader_after.list() == []
