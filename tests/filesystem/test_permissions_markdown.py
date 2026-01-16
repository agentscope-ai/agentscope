# -*- coding: utf-8 -*-
"""Tests for human-readable grants markdown export."""
from __future__ import annotations

from agentscope.filesystem._memory import InMemoryFileSystem
from agentscope.filesystem._service import FileDomainService


def test_handle_describe_grants_markdown() -> None:
    fs = InMemoryFileSystem()
    handle = fs.create_handle(
        [
            {"prefix": "/proc/", "ops": {"list", "file", "read_file"}},
            {
                "prefix": "/tmp/",
                "ops": {"list", "file", "read_file", "write", "delete"},
            },
        ]
    )

    summary = handle.describe_grants_markdown()
    assert (
        summary
        == "/proc/: ls, stat, read\n/tmp/: ls, stat, read, write, delete"
    )


def test_service_describe_permissions_markdown() -> None:
    fs = InMemoryFileSystem()
    handle = fs.create_handle(
        [
            {"prefix": "/proc/", "ops": {"list", "file", "read_file"}},
            {
                "prefix": "/tmp/",
                "ops": {"list", "file", "read_binary", "write"},
            },
        ]
    )
    svc = FileDomainService(handle)
    summary = svc.describe_permissions_markdown()
    assert summary == "/proc/: ls, stat, read\n/tmp/: ls, stat, read, write"
