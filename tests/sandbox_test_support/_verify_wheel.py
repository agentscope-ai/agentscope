# -*- coding: utf-8 -*-
"""Verify installed package bytes using only the gateway standard library."""

import sys
from importlib.metadata import distribution
from pathlib import Path
from zipfile import ZipFile


def verify_wheel(wheel: str) -> None:
    """Fail if any installed AgentScope file differs from the wheel."""
    installed = distribution("agentscope")
    with ZipFile(wheel) as archive:
        names = [
            name
            for name in archive.namelist()
            if name.startswith("agentscope/") and not name.endswith("/")
        ]
        if not names:
            raise RuntimeError(f"Wheel contains no AgentScope files: {wheel}")
        for name in names:
            path = Path(installed.locate_file(name))
            if path.read_bytes() != archive.read(name):
                raise RuntimeError(
                    f"Installed file differs from wheel: {name}",
                )


if __name__ == "__main__":
    verify_wheel(sys.argv[1])
