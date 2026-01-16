# -*- coding: utf-8 -*-
from __future__ import annotations

import pytest

from agentscope.filesystem import InMemoryFileSystem, AccessDeniedError


ALL_READ = {"list", "file", "read_binary", "read_file", "read_re"}


def test_handle_permissions_union_and_denials() -> None:
    fs = InMemoryFileSystem()
    handle = fs.create_handle(
        [
            {"prefix": "/userinput/", "ops": set(ALL_READ)},
            {
                "prefix": "/workspace/",
                "ops": set(ALL_READ) | {"write", "delete"},
            },
        ]
    )

    # write/read/delete cycle is allowed in /workspace
    handle.write("/workspace/a.txt", "data")
    assert handle.read_file("/workspace/a.txt") == "data"
    handle.delete("/workspace/a.txt")

    # /userinput is read-only: write should be denied regardless of content
    with pytest.raises(AccessDeniedError):
        handle.write("/userinput/a.txt", "nope")
