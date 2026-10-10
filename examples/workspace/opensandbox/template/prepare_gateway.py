# -*- coding: utf-8 -*-
"""Prepare a session-free gateway image without importing the workspace SDK."""

import hashlib
from importlib.metadata import distribution
import json
from pathlib import Path


def prepare(root: Path = Path("/")) -> dict:
    """Install gateway/helper scripts and write the versioned layout marker."""
    home = root / "root/.agentscope"
    for directory in (
        home,
        root / "workspace/data",
        root / "workspace/skills/.seed",
        root / "workspace/sessions",
    ):
        directory.mkdir(parents=True, exist_ok=True)
    package = distribution("agentscope")
    files = {
        "gateway_script_sha256": (
            "agentscope/workspace/_mcp_gateway/_mcp_gateway_app.py",
            "_mcp_gateway_app.py",
        ),
        "glob_helper_sha256": (
            "agentscope/tool/_builtin/_scripts/_glob_helper.py",
            "_glob_helper.py",
        ),
    }
    state = {
        "schema_version": 1,
        "workdir": "/workspace",
        "gateway_home": "/root/.agentscope",
        "gateway_port": 5600,
    }
    for key, (source, target) in files.items():
        data = package.locate_file(source).read_bytes()
        (home / target).write_bytes(data)
        state[key] = hashlib.sha256(data).hexdigest()
    (home / "template-state.json").write_text(json.dumps(state) + "\n")
    return state


if __name__ == "__main__":
    prepare()
