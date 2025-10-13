# -*- coding: utf-8 -*-
"""Tests for the builtin namespace configuration helpers."""
import pytest

from agentscope.filesystem import (
    AccessDeniedError,
    BuiltinFileSystem,
    NotFoundError,
    builtin_grant,
    builtin_grants,
)


def test_userinput_handle_is_read_only() -> None:
    fs = BuiltinFileSystem()
    seeder_grant = builtin_grant("userinput")
    seeder_grant["ops"].add("write")
    seeder = fs.create_handle([seeder_grant])
    seeder.write("/userinput/corpus.txt", "seed")

    user_handle = fs.create_userinput_handle()
    assert user_handle.read_file("/userinput/corpus.txt") == "seed"

    with pytest.raises(AccessDeniedError):
        user_handle.write("/userinput/corpus.txt", "overwrite")

    with pytest.raises(AccessDeniedError):
        user_handle.delete("/userinput/corpus.txt")


def test_workspace_handle_supports_full_mutation_cycle() -> None:
    fs = BuiltinFileSystem()
    workspace_handle = fs.create_workspace_handle()
    workspace_handle.write("/workspace/out.txt", "payload")
    assert workspace_handle.read_file("/workspace/out.txt") == "payload"
    workspace_handle.delete("/workspace/out.txt")
    with pytest.raises(NotFoundError):
        workspace_handle.read_file("/workspace/out.txt")


def test_internal_handle_cannot_delete_logs() -> None:
    fs = BuiltinFileSystem()
    internal_writer = fs.create_internal_handle()
    internal_writer.write("/internal/run.log", "log-entry")
    assert internal_writer.read_file("/internal/run.log") == "log-entry"

    with pytest.raises(AccessDeniedError):
        internal_writer.delete("/internal/run.log")


def test_combined_handle_creation() -> None:
    fs = BuiltinFileSystem()
    combined = fs.create_handle_for(["internal", "workspace"])
    combined.write("/workspace/a.txt", "data")
    combined.write("/internal/logs.txt", "log")
    assert combined.read_file("/internal/logs.txt") == "log"
    combined.delete("/workspace/a.txt")
