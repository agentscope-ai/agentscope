# -*- coding: utf-8 -*-
"""Build-time readiness gate for an empty, running workspace gateway."""

import json
from pathlib import Path
from urllib.request import urlopen


def check() -> None:
    """Confirm gateway readiness and an empty session-free layout."""
    home = Path("/root/.agentscope")
    state = json.loads((home / "template-state.json").read_bytes())
    assert state["schema_version"] == 1
    workspace = Path(state["workdir"])
    assert {p.name for p in workspace.iterdir()} == {
        "data",
        "skills",
        "sessions",
    }
    for name in ("data", "sessions"):
        assert not any((workspace / name).iterdir())
    assert {p.name for p in (workspace / "skills").iterdir()} == {".seed"}
    url = f"http://127.0.0.1:{state['gateway_port']}"
    with urlopen(url + "/health", timeout=1) as response:
        assert response.status == 200
    with urlopen(url + "/mcps", timeout=1) as response:
        assert json.load(response) == []


if __name__ == "__main__":
    check()
