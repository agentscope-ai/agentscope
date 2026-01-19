# -*- coding: utf-8 -*-
from __future__ import annotations

import os

from agentscope.filesystem import DiskFileSystem


def test_disk_default_root_creates_output_tree(tmp_path) -> None:
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        fs = DiskFileSystem()
        root = os.path.commonpath(
            [
                fs._internal_dir,
                fs._userinput_dir,
                fs._workspace_dir,
            ],  # type: ignore[attr-defined]
        )
        assert os.path.basename(os.path.dirname(root)) == "output"
        assert os.path.isdir(fs._internal_dir)  # type: ignore[attr-defined]
        assert os.path.isdir(fs._userinput_dir)  # type: ignore[attr-defined]
        assert os.path.isdir(fs._workspace_dir)  # type: ignore[attr-defined]
        marker = os.path.join(
            fs._internal_dir,
            ".created",
        )  # type: ignore[attr-defined]
        assert os.path.isfile(marker)
    finally:
        os.chdir(cwd)
