# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import pytest

from agentscope.filesystem import DiskFileSystem, AccessDeniedError
from agentscope.filesystem._service import FileDomainService


def test_edit_file_applies_ordered_replacements(tmp_path) -> None:
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        fs = DiskFileSystem()
        handle = fs.create_handle(
            [
                {
                    "prefix": "/userinput/",
                    "ops": {"list", "file", "read_file"},
                },
                {
                    "prefix": "/workspace/",
                    "ops": {"list", "file", "read_file", "write", "delete"},
                },
            ],
        )
        svc = FileDomainService(handle)

        svc.write_file("/workspace/a.txt", "hello world\nalpha beta\n")
        meta = svc.edit_file(
            "/workspace/a.txt",
            edits=[
                {"oldText": "hello", "newText": "hi"},
                {"oldText": "beta", "newText": "gamma"},
            ],
        )
        assert meta["path"] == "/workspace/a.txt"
        out = handle.read_file("/workspace/a.txt")
        assert "hi world" in out and "gamma" in out

        # editing under /userinput is denied regardless of existence
        with pytest.raises(AccessDeniedError):
            svc.edit_file(
                "/userinput/corpus.txt",
                edits=[{"oldText": "x", "newText": "y"}],
            )
    finally:
        os.chdir(cwd)
